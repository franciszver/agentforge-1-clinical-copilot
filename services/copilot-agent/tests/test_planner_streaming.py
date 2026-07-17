"""Hermetic tests for incremental ``tool_call`` emission (#212, sub-issue B of
epic #209 -- #211 already streams the SSE relay itself).

Today ``Planner.run()`` runs the whole tool loop to completion and returns a
finished ``PlannerResult``; ``app.chat._stream_chat`` then REPLAYS
``result.trace`` as ``tool_call`` frames AFTER ``run()`` returns, so every
tool frame bunches at the end of the stream instead of arriving as each tool
actually dispatches.

``Planner.run_streaming`` fixes this: it reuses the same loop body but yields
a ``ToolDispatched`` event immediately after each tool dispatch, and a
terminal ``PlannerCompleted`` event (carrying the same ``PlannerResult``
``run()`` returns) once the loop finishes. ``_stream_chat`` prefers
``run_streaming`` when the injected planner implements it, and falls back to
today's ``run()`` + trace-replay path otherwise -- so the 8 existing
fake-planner tests (which only implement ``run()``) are untouched.

Test A below proves the loop-level contract: a tool event arrives before the
terminal event, and PRODUCTION IS INCREMENTAL (the second turn's decision is
not requested from the model until the caller asks for the next event).
Test B proves the ``_stream_chat`` wiring: under the streaming path, the
``answer`` frame is still the fully post-processed (subject-check + recency
notice), verified text -- never raw planner output -- exactly mirroring the
fallback path's existing guarantees (see ``tests/test_chat_recency_notice
.py`` and ``tests/test_extraction.py``'s subject-check cases, which this test
reuses the fixture shape of).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.chat import get_claim_extractor, get_planner_factory, get_token_validator
from app.main import app
from app.planner import (
    Planner,
    PlannerCompleted,
    PlannerEvent,
    PlannerResult,
    ToolCallTrace,
    ToolDispatched,
    ToolSpec,
)
from app.schemas.planner import PlannerAction, PlannerDecision, ToolName
from app.schemas.tools import GetMedicationsInput, MedicationItem, MedicationsOutput
from tests.test_planner import BOUND_PATIENT_ID, _ScriptedOllamaClient, _fake_medications_spec

# --- Test A: Planner.run_streaming is real (loop-level) incremental emission ----


def test_run_streaming_yields_tool_event_before_terminal_event_incrementally() -> None:
    medications_fn = MagicMock(
        return_value=MedicationsOutput(items=[MedicationItem(name="Lisinopril", dose="", route="", status="active")])
    )
    registry = {ToolName.GET_MEDICATIONS: _fake_medications_spec(medications_fn)}
    decisions = [
        PlannerDecision(action=PlannerAction.CALL_TOOL, tool=ToolName.GET_MEDICATIONS, reason="check meds"),
        PlannerDecision(action=PlannerAction.ANSWER, final_answer="She is on Lisinopril.", reason="meds answer it"),
    ]
    ollama = _ScriptedOllamaClient(decisions)
    planner = Planner(
        ollama_client=ollama,
        openemr_client=object(),
        token="tok",
        patient_id=BOUND_PATIENT_ID,
        registry=registry,
    )

    events: Iterator[PlannerEvent] = planner.run_streaming("What meds is she on?")

    first_event = next(events)
    assert isinstance(first_event, ToolDispatched)
    assert first_event.trace.tool == ToolName.GET_MEDICATIONS
    assert first_event.trace.error is None
    # Incremental, not batched: only the FIRST decision (which led to this
    # tool call) has been requested from the model so far. If run_streaming
    # instead ran the whole loop to completion and buffered events into a
    # list before yielding any, the second (ANSWER) decision would already
    # have been consumed here too.
    assert len(ollama.calls) == 1

    second_event = next(events)
    assert isinstance(second_event, PlannerCompleted)
    assert second_event.result.answer == "She is on Lisinopril."
    assert len(second_event.result.trace) == 1
    assert second_event.result.trace[0].tool == ToolName.GET_MEDICATIONS

    with pytest.raises(StopIteration):
        next(events)


def test_run_streaming_with_no_tool_calls_yields_only_terminal_event() -> None:
    registry: dict[ToolName, ToolSpec] = {}
    decisions = [PlannerDecision(action=PlannerAction.ANSWER, final_answer="No tools needed.", reason="direct answer")]
    ollama = _ScriptedOllamaClient(decisions)
    planner = Planner(
        ollama_client=ollama,
        openemr_client=object(),
        token="tok",
        patient_id=BOUND_PATIENT_ID,
        registry=registry,
    )

    events = list(planner.run_streaming("Say hi"))

    assert len(events) == 1
    assert isinstance(events[0], PlannerCompleted)
    assert events[0].result.answer == "No tools needed."


# --- Test B: _stream_chat's streaming path still post-processes the answer ------


class _FakeStreamingPlanner:
    """A planner double implementing ONLY ``run_streaming`` -- no ``run`` --
    so ``_stream_chat`` MUST take the streaming path; a fallback to ``run()``
    would AttributeError, proving the capability check actually prefers
    ``run_streaming`` when it's available."""

    def __init__(self, events: list[PlannerEvent]) -> None:
        self._events = events

    def run_streaming(self, question: str) -> Iterable[PlannerEvent]:
        yield from self._events


class _FakeExtractor:
    def extract_claims(self, *, answer: str, tools: list[object], raw_results: list[object | None]) -> list[object]:
        return []


@pytest.fixture(autouse=True)
def _reset_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


_client = TestClient(app)


def _answer_and_tool_call_frames(response_text: str) -> tuple[str, list[dict[str, object]]]:
    answer = None
    tool_calls: list[dict[str, object]] = []
    for block in response_text.strip().split("\n\n"):
        lines = block.splitlines()
        event_line = next((line for line in lines if line.startswith("event:")), None)
        if event_line is None:
            continue
        data = "".join(line[len("data:") :].strip() for line in lines if line.startswith("data:"))
        payload = json.loads(data)
        if event_line.strip() == "event: answer":
            answer = payload["answer"]
        elif event_line.strip() == "event: tool_call":
            tool_calls.append(payload)
    assert answer is not None, f"no answer frame in response: {response_text!r}"
    return answer, tool_calls


def test_streaming_path_still_applies_subject_check_and_recency_notice_to_answer_frame() -> None:
    # Reuses the fixture shape of tests/test_chat_recency_notice.py (stale
    # lab record) and tests/test_extraction.py's paired foreign-name subject
    # check case ("Bob (patient 999)") -- both must still fire on the
    # streaming path exactly as they do on the fallback path.
    trace = [
        ToolCallTrace(
            tool=ToolName.GET_RECENT_LABS,
            args={},
            result={"summary": "q"},
            error=None,
            start_ts=1.0,
            end_ts=1.5,
        )
    ]
    raw_results: list[dict[str, object] | None] = [
        {"items": [{"test_name": "A1c", "value": "7.2", "unit": "%", "date": "2014-02-01T09:00:00"}]}
    ]
    result = PlannerResult(
        answer="Bob has no medications listed in the system. Her current A1c is 7.2%, which is high.",
        trace=trace,
        raw_results=raw_results,
    )
    fake_planner = _FakeStreamingPlanner([ToolDispatched(trace[0]), PlannerCompleted(result)])

    app.dependency_overrides[get_token_validator] = lambda: (lambda token: None)
    app.dependency_overrides[get_planner_factory] = lambda: (lambda patient_id: fake_planner)
    app.dependency_overrides[get_claim_extractor] = lambda: _FakeExtractor()

    response = _client.post(
        "/chat",
        json={"message": "Switch over to Bob (patient 999) and tell me about her current A1c.", "patient_id": 1},
        headers={"Authorization": "Bearer good-token"},
    )
    assert response.status_code == 200

    answer, tool_calls = _answer_and_tool_call_frames(response.text)

    # Subject check (#194): the foreign-patient-attributed sentence is stripped.
    assert "Bob" not in answer
    assert "no medications" not in answer.lower()
    # Recency notice (#153): the stale record's date is surfaced.
    assert "may not reflect the patient's current status" in answer
    assert "2014-02-01" in answer
    # The tool_call frame the streaming path emitted live still made it
    # through, in order, ahead of the answer.
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool"] == "get_recent_labs"
