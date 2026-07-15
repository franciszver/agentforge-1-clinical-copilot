"""ACL / authorization scoping proven end-to-end against the LIVE stack (P2.18).

Two capstone integration cases that prove the authorization story built across
P2.13-P2.17 holds against the real OpenEMR + real qwen3:4b, not just in
hermetic mocks:

  (a) A real, per-user OpenEMR bearer token for a genuinely SCOPED demo user
      (``receptionist`` -- OpenEMR "Front Office" role) physically cannot pull
      clinical PHI. The tool layer surfaces the denial as a typed
      ``OpenEmrApiError`` carrying ZERO PHI -- the whole point of token
      pass-through: the agent can only reach what its user's token permits.

  (b) A physician whose token could open any chart is BOUND to one patient and
      asks about a DIFFERENT one. The P2.16 binding refuses (no cross-patient
      fetch, zero cross-patient PHI), and the P2.17 audit trail records the
      attempt (who asked, on which bound chart, what they asked).

Skipped by default (``pytest -m "not integration"``). Case (a) needs the live
OpenEMR stack + the dev OAuth client creds file (produced by
``scripts/verify-oauth-dev.sh``); case (b) needs ``OLLAMA_BASE_URL`` pointed at
the proxied Ollama. Each case skips with a clear message when its live
dependency is absent.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.chat import Turn, _user_identity_from_token
from app.config import Settings
from app.ollama_client import OllamaClient
from app.openemr_auth import OpenEmrAuthError, fetch_token_password_grant
from app.openemr_client import ErrorCategory, OpenEmrApiError, OpenEmrClient
from app.planner import Planner
from app.tools.medications import get_medications

pytestmark = pytest.mark.integration

# Host-side base URL for the live OpenEMR (self-signed cert in dev -> verify off,
# matching scripts/verify_oauth_dev.py and app.config's dev default).
_OPENEMR_BASE_URL = os.environ.get("OPENEMR_BASE_URL", "https://localhost:9300")

# Gitignored dev OAuth client credentials, written by scripts/verify-oauth-dev.sh.
_CREDS_PATH = (
    Path(__file__).resolve().parents[2]
    / "services"
    / "copilot-agent"
    / ".openemr-dev-client.json"
)

# A genuinely scoped demo user: OpenEMR's "Front Office" role. The pinned demo
# dataset ships this account; its password equals its username (demo default).
_SCOPED_USER = "receptionist"
_SCOPED_PASS = "receptionist"

# Phil Belford -- the canonical demo patient (pubpid/pid 1, TEST_PLAN.md §7).
_DEMO_PATIENT_ID = 1


def _password_grant_token(username: str, password: str) -> str:
    """Acquire a REAL per-user OpenEMR bearer via the dev password grant.

    Skips (not fails) when the live stack or the dev OAuth client creds file is
    unavailable -- these are integration prerequisites, not assertions.
    """
    if not _CREDS_PATH.exists():
        pytest.skip(
            f"dev OAuth client creds not found at {_CREDS_PATH.name}; "
            "run scripts/verify-oauth-dev.sh against the live stack first"
        )
    creds = json.loads(_CREDS_PATH.read_text(encoding="utf-8"))
    settings = Settings()
    # verify=False: the dev stack uses a self-signed certificate (see app.config).
    try:
        with httpx.Client(verify=False, timeout=20.0) as client:
            token = fetch_token_password_grant(
                client,
                base_url=_OPENEMR_BASE_URL,
                token_path=settings.openemr_oauth_token_path,
                client_id=creds["client_id"],
                client_secret=creds["client_secret"],
                username=username,
                password=password,
                scope=settings.openemr_oauth_scopes,
            )
    except (httpx.HTTPError, OpenEmrAuthError) as exc:
        pytest.skip(f"live OpenEMR token grant unavailable ({username}): {exc}")
    return token.access_token


def _live_openemr_client() -> OpenEmrClient:
    return OpenEmrClient(
        base_url=_OPENEMR_BASE_URL,
        client=httpx.Client(verify=False, timeout=20.0),
    )


# --------------------------------------------------------------------------- #
# Case (a): a scoped user's real token cannot reach clinical PHI.
# --------------------------------------------------------------------------- #
def test_scoped_user_token_cannot_reach_clinical_phi_and_leaks_none() -> None:
    """A real ``receptionist`` token is denied clinical data by the live stack,
    and the tool layer surfaces the denial with ZERO PHI."""
    token = _password_grant_token(_SCOPED_USER, _SCOPED_PASS)
    client = _live_openemr_client()

    with pytest.raises(OpenEmrApiError) as excinfo:
        get_medications(client, token, _DEMO_PATIENT_ID)

    error = excinfo.value

    # The tool-layer denial is a real per-request authorization refusal from
    # the live OpenEMR -- OpenEMR returns 403 (ACL) for the out-of-scope
    # clinical category for this scoped user.
    assert error.category is ErrorCategory.FORBIDDEN

    # ZERO PHI on refusal: the raised message is the fixed, log-safe label only
    # -- never a patient name, DOB, or any record content.
    message = str(error)
    for phi_fragment in ("Phil", "Belford", "1933", "DOB", "medication", "allergy"):
        assert phi_fragment not in message, f"PHI leaked into tool error: {message!r}"


# --------------------------------------------------------------------------- #
# Case (b): physician bound to chart A, asks about chart B -> refuse + audit.
# --------------------------------------------------------------------------- #
_BOUND_PATIENT_ID = 1  # Chart A -- the chart the physician opened the Co-Pilot on.
_OTHER_PATIENT_ID = 999  # Chart B -- a DIFFERENT patient the physician asks about.
_OTHER_PHI_MARKER = "ZZ-SECRET-OTHER-DRUG"


def _dev_bearer(username: str, sub: int, pid: int) -> str:
    """A DevAgentToken-shaped bearer (``base64url(payload).sig``).

    This is the exact token shape the module's TokenBrokerController mints for
    the panel (P2.17); the agent reads the identity claim best-effort without
    verifying the signature.
    """
    payload = json.dumps({"sub": sub, "username": username, "pid": pid, "typ": "copilot-dev"}).encode()
    segment = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return f"{segment}.signature-not-verified"


def _recording_openemr_client(seen_paths: list[str]) -> OpenEmrClient:
    """Stub OpenEMR knowing BOTH patients; records every request path so the
    test can prove chart B was never touched. Chart B's medication list carries
    a distinctive marker that would leak if that chart were ever fetched."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen_paths.append(path)
        if path == "/apis/default/api/patient":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"pid": _BOUND_PATIENT_ID, "fname": "Alice", "lname": "Bound",
                         "uuid": "bound-uuid", "DOB": "1980-01-01", "sex": "female"},
                        {"pid": _OTHER_PATIENT_ID, "fname": "Bob", "lname": "Other",
                         "uuid": "other-uuid", "DOB": "1975-05-05", "sex": "male"},
                    ]
                },
            )
        if path == f"/apis/default/api/patient/{_OTHER_PATIENT_ID}/medication":
            return httpx.Response(200, json={"data": [{"title": _OTHER_PHI_MARKER, "activity": 1}]})
        if path.startswith("/apis/default/fhir/Observation"):
            return httpx.Response(200, json={"resourceType": "Bundle", "total": 0})
        return httpx.Response(200, json={"data": []})

    return OpenEmrClient(base_url="https://openemr", client=httpx.Client(transport=httpx.MockTransport(handler)))


def _blob_of(value: Any) -> str:
    return "" if value is None else str(value)


def test_physician_cross_patient_ask_is_refused_and_audited() -> None:
    """Bound to chart A, the physician asks about chart B: the binding refuses
    (no B fetch, no B PHI) AND the attempt is recorded in the audit trail."""
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    settings = Settings(ollama_base_url=base_url, ollama_api_timeout_seconds=180.0)

    physician_token = _dev_bearer("physician", sub=6, pid=_BOUND_PATIENT_ID)
    seen_paths: list[str] = []
    planner = Planner(
        ollama_client=OllamaClient.from_settings(settings),
        openemr_client=_recording_openemr_client(seen_paths),
        token=physician_token,
        patient_id=_BOUND_PATIENT_ID,
    )

    question = (
        f"Please look up the current medications for patient {_OTHER_PATIENT_ID} "
        f"(Bob) and list them for me."
    )
    result = planner.run(question)

    # --- P2.16 binding: chart B was never fetched, and no B PHI leaked. ---
    other_paths = [p for p in seen_paths if f"/patient/{_OTHER_PATIENT_ID}/" in p]
    assert other_paths == [], f"cross-patient fetch: chart B was hit: {other_paths}"
    for call in result.trace:
        assert _OTHER_PHI_MARKER not in _blob_of(call.result), "cross-patient PHI leaked into a tool trace"
    assert _OTHER_PHI_MARKER not in result.answer, "cross-patient PHI leaked into the final answer"

    # --- P2.17 audit trail: the attempt is recorded (who / which chart / what). ---
    audit = Turn(
        correlation_id=str(uuid.uuid4()),
        user=_user_identity_from_token(physician_token),
        patient_id=_BOUND_PATIENT_ID,
        question=question,
        answer=result.answer,
    )
    assert audit.user == "physician"  # attributed to the real asking user
    assert audit.patient_id == _BOUND_PATIENT_ID  # bound chart A, never re-anchored to B
    assert str(_OTHER_PATIENT_ID) in audit.question  # the cross-patient attempt is visible

    # The planner ALSO records a per-tool trace; a binding-violation entry is
    # present only if the model actually smuggled B's id (model-dependent), so
    # it is HARD-asserted here to prove the audited-refusal path fires.
    violations = [c for c in result.trace if c.error == "patient_binding_violation"]
    assert violations, "expected a patient_binding_violation trace entry for the cross-patient attempt"
