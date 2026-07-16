# copilot-agent

FastAPI service for the Clinical Co-Pilot agent.

## Tests

```bash
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

## OpenEMR OAuth (dev token flow)

`app/openemr_auth.py` registers a confidential OAuth2 client and obtains a
user bearer token so the agent can call OpenEMR APIs. To verify end-to-end
against a running dev stack:

```bash
bash scripts/verify-oauth-dev.sh
```

This registers a confidential client, enables it, fetches a token via the
password grant, and calls `GET /apis/default/fhir/Patient` (expects HTTP 200;
an empty Bundle is fine — demo data is seeded in P2.0). It also proves the
bad-credential path fails cleanly.

**DEV-ONLY.** Two shortcuts here are for the local dev loop only and must not
ship to production:

- **Password grant** (username/password → token). Production uses the OAuth2
  `authorization_code` grant (per plan §4.2).
- **Enabling the client via direct SQL.** OpenEMR registers new clients
  *disabled*; production enables them through admin UI/approval.

TLS verification is off because the dev stack uses a self-signed certificate
(`openemr_verify_ssl` defaults to `False`; set it `True` where a real cert is
enforced). The dev client secret and tokens are written only to the gitignored
`services/copilot-agent/.openemr-dev-client.json` and are never printed or
committed.

## OpenEMR OAuth (production authorization_code client) — #124 Phase 1

Production does **not** use the password grant. It uses the OAuth2
`authorization_code` grant against a confidential client that a browser drives
through OpenEMR's authorize/callback. Phase 1 (this change) sets up only the
**client registration**; the authorize/callback endpoints and token brokering
land in later phases.

Register the production client (runs inside the agent container, which is the
only host that can reach OpenEMR on the internal network):

```bash
bash scripts/register-copilot-prod-client.sh
```

This posts a confidential (`application_type: private`) registration with the
`authorization_code` + `refresh_token` grants and:

- **Canonical `redirect_uri`** — the single source of truth in
  `app/config.py` (`copilot_prod_client_redirect_uri`):
  `https://localhost:9300/interface/modules/custom_modules/oe-module-clinical-copilot/public/oauth-callback.php`.
  This is the **browser-facing** host and the module's one-file-per-route OAuth
  callback (Phase 2 serves it). It deliberately differs from the internal
  `openemr` docker alias used for the server-side registration call. Phase 2's
  authorize/callback must match this URL byte-for-byte — OpenEMR enforces exact
  `redirect_uri` matching.
- **Reconciled SMART scopes** (`copilot_prod_client_scopes`):
  `openid offline_access launch launch/patient api:oemr api:fhir fhirUser`.
  Reconciled against OpenEMR's
  `ServerScopeListEntity::getAllSupportedScopesList()`, which enumerates only
  literal scopes and **silently strips** anything unrecognized. `user/*.read`
  (from the SMART starting set) is **dropped** — OpenEMR has no wildcard scope,
  so the `user/*` lookup key has no validator entry and is discarded with no
  error to the client. Explicit per-resource read scopes (as in
  `copilot_dev_token_scopes`) are requested at **authorize time in Phase 2**,
  not baked into registration.

**PKCE (S256):** nothing is declared at registration. PKCE is a redirect-time
concern — the `code_challenge` travels on the Phase 2 authorize request, not in
client-registration metadata (RFC 7591) — and OpenEMR's `CustomAuthCodeGrant`
supports S256 at the grant level. Phase 2's authorize/callback will use PKCE
(S256) since the code transits a browser redirect.

### Admin approval (production) vs. the dev SQL shortcut

OpenEMR registers every new client **disabled**. The two paths to enable it:

- **Production (real step):** an OpenEMR admin approves/enables the client in
  the admin UI — **Administration → Config → Connectors → OAuth2 Clients** —
  and enables the `copilot-agent-prod` client. No database is touched by hand.
- **Dev-only shortcut:** `scripts/bootstrap-copilot-dev-client.sh` runs
  `UPDATE oauth_clients SET is_enabled=1 …` directly against the dev database.
  This is a **dev-loop convenience only** and must never be used in production.

The production client secret is written only to the container-local
`copilot_prod_client_creds_path` (default `/data/openemr-prod-client.json`) and
is never printed or committed.

## Container

```bash
docker build -t copilot-agent:dev .
docker run -d --rm -p 8099:8000 --name copilot-agent-test copilot-agent:dev
curl http://localhost:8099/health
docker stop copilot-agent-test
```
