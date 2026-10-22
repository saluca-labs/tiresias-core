# tiresias-core

The source-available core of Tiresias: a self-hosted, OpenAI-compatible LLM proxy in Python that routes across providers, encrypts what it logs, tracks tokens and cost per session, and checks requests against YAML policies, with a dashboard API over the stored data.

## Status

Source-available core, version 1.0.0 in `pyproject.toml`. Not published to PyPI. What is verified in this repository on 2026-10-22: the test suite passes (295 passed, 40 known failures marked `xfail`, see `tests/known_failures.txt`), and the proxy starts from a source install and answers `/health` (see Quickstart).

What does not work as previously documented: `docker compose up -d` fails, and the steps in `QUICKSTART.md` do not produce a running stack. See the correction below.

> **Correction, 2026-10-22.** Earlier versions of this README described the wider Tiresias platform as if it were in this repository, and gave a quickstart that does not run. Checked against `origin/main` at 3c634b6:
>
> - **Quickstart.** `docker compose up -d` fails before starting anything: `docker-compose.yml` builds a `relay` service from `Dockerfile.relay`, which is not in the repository. Started without the relay, the proxy container exits because the compose file passes `ENCRYPTION_KEY`, while the code reads `TIRESIAS_KEK`; the dashboard container exits because its command loads `tiresias.dashboard.app:app`, and that module only defines the factory `create_dashboard_app`. The `.env.example` names (`TIRESIAS_LICENSE_TOKEN`, `ENCRYPTION_KEY`, `DATABASE_URL`) are not read by the code, and it has no `TIRESIAS_TENANT_ID` line although the README told you to set one. The dashboard API is served by the proxy under `/dash/v1/...`, not on port 3000.
> - **Soulkeys.** Described as "persistent cryptographic identity for AI agents, SHA-512, tenant-scoped, revocable". This repository has no soulkey issuance or SHA-512. The dashboard accepts an `X-SoulKey` header and asks an external SoulAuth service (`SOULAUTH_URL`) whether it is valid, or accepts an API key checked against its stored SHA-256 hash.
> - **Audit.** Described as "structured audit events for 15+ event types". `audit/logger.py` accepts four event types (`sop_check`, `sop_grant`, `sop_deny`, `sop_violation`). The per-request and per-response SHA-256 content hashes are real (`proxy/interceptor.py`).
> - **Policy engine.** "Capability tokens (JWT)" are not in this repository. The YAML policies, the policy decision point and `POST /v1/auth/evaluate-sop` are.
> - **Anomaly detection.** "18 detection types, behavioral baselines, real-time alerting, SIEM integration (Splunk, Elastic, Syslog, Sentinel)" are not in this repository.
> - **Comparison table.** The "Why Tiresias" table marked this core "Yes" for agent identity, CoT auditing and anomaly detection, which it does not have, and made claims about six other products that were not re-checked. It has been removed rather than corrected row by row; it remains in the git history.
> - **MASP section.** It said Tiresias gives "identity, audit, policy, detection, and cost attribution" dropped into any agent platform. Detection and identity issuance are not in this repository; the section is removed for the same reason.

## Install

Tested 2026-10-22 on Windows with Python 3.12 and uv 0.11; CI runs Python 3.11 and 3.12 on Ubuntu. `requires-python` is `>=3.11`.

```bash
git clone https://github.com/saluca-labs/tiresias-core.git
cd tiresias-core
uv sync --locked --extra dev
```

The container image builds (`docker compose build proxy`), but the compose stack does not start as shipped; see the correction above.

## Quickstart

Run the proxy from the source install. `TIRESIAS_KEK` is the local key-encryption key (any 64-hex value for a trial), `TIRESIAS_DATA_ROOT` is where per-tenant SQLite databases go (the default `/data` is usually not writable).

```bash
export TIRESIAS_KEK=$(python -c "import secrets; print(secrets.token_hex(32))")
export TIRESIAS_DATA_ROOT=./data
export TIRESIAS_TENANT_ID=demo
uv run uvicorn tiresias.proxy.app:app --port 8080
```

In another terminal, from a run on 2026-10-22:

```text
$ curl http://127.0.0.1:8080/health
{"status":"ok","service":"tiresias-proxy","mode":"onprem"}
```

To forward real requests, set at least one provider key before starting (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`) and list providers in `TIRESIAS_PROVIDERS` (default `openai`). With no key set, `POST /v1/chat/completions` returns `502 {"detail":"All providers exhausted. ..."}`, which is what the run above returned. A provider round trip was not run for this README.

## Usage

Point an OpenAI-compatible client at the proxy:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8080/v1", api_key="any-value")  # provider keys come from the proxy's env
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello from Tiresias!"}],
)
```

Main routes (`src/tiresias/proxy/app.py`):

| Route | What it does |
|---|---|
| `GET /health` | Liveness and mode |
| `POST /v1/chat/completions` | OpenAI-compatible chat, streaming or not, routed through the provider cascade |
| `POST /v1/sessions/{id}/tag`, `GET /v1/sessions/{id}` | Session tagging and lookup; send `x-tiresias-session-id` on requests |
| `GET /v1/admin/providers`, `POST /v1/admin/reload` | Provider cascade status and reload |
| `POST /v1/auth/evaluate-sop` | Check an action against the YAML SOP policies |
| `GET /dash/v1/spend`, `/requests`, `/latency`, `/errors`, `/sessions/top`, `/sessions/{id}/replay`, `/traces`, `/providers/health` | Dashboard API over the stored data |
| `/api/{path}` | Generic reverse proxy to `TIRESIAS_UPSTREAM_URL` with per-endpoint telemetry |
| `GET /v1/analytics/api/endpoints`, `/costs`, `/errors`, `GET /v1/analytics/unified` | Analytics over the generic proxy traffic |

Configuration is environment variables, read by `src/tiresias/config.py` (`TiresiasSettings`). The ones most often needed: `TIRESIAS_MODE` (`onprem`, `dedicated`, `saas`), `TIRESIAS_TENANT_ID`, `TIRESIAS_KEK_PROVIDER` (only `local` works in core; the others raise "requires Tiresias Enterprise"), `TIRESIAS_KEK`, `TIRESIAS_DATA_ROOT`, `TIRESIAS_DATABASE_URL` (a `postgresql://` URL switches from per-tenant SQLite to one Postgres database), `TIRESIAS_PROVIDERS`, `TIRESIAS_RETENTION_DAYS`.

## Architecture

```text
client --> proxy (FastAPI) --> provider cascade --> OpenAI / Anthropic / Gemini / Groq / Ollama
              |
              +--> interceptor: token count, cost, SHA-256 request/response hashes
              +--> envelope encryption (per-tenant DEK, local KEK) --> SQLite per tenant, or Postgres
              +--> policy decision point (YAML policies, rate limit, spend)
              +--> dashboard API (/dash/v1/...)
```

- `src/tiresias/proxy/`: the FastAPI app, request interception, rate limiting, generic API proxy, SaaS-mode API-key auth.
- `src/tiresias/providers/`: provider clients, the cascade router and health tracking.
- `src/tiresias/encryption/`: AEAD and envelope encryption; `providers/local.py` is the only KEK provider core enables.
- `src/tiresias/tracking/`: token counting, model pricing, sessions.
- `src/tiresias/auth/`, `src/tiresias/policy/`, `policies/`: policy decision point, rate limiter, spend tracker, YAML policy loading.
- `src/tiresias/dashboard/`: dashboard router (mounted in the proxy), a standalone app factory and static UI files, and dashboard auth.
- `src/tiresias/storage/`: SQLAlchemy engine, schema, retention purge.

## Citation

No archived record yet.

## License

Functional Source License 1.1, Apache 2.0 Future License (FSL-1.1-Apache, SPDX `FSL-1.1-ALv2`), see [LICENSE](./LICENSE). Each release becomes available under the Apache License 2.0 two years after it ships. Copyright Saluca LLC.

GitHub shows the licence as "Other" because its licence detector does not include the FSL. Note that `NOTICE` and the `NOTICE` files in `src/tiresias/analytics/`, `providers/` and `proxy/` still say "Licensed under the Apache License, Version 2.0", left from before the 2026-08-18 relicence. `LICENSE` is authoritative.

| Module | License | Description |
|--------|---------|-------------|
| `tiresias-core` (this repository) | FSL-1.1-Apache | Proxy, provider routing, encryption, token and cost tracking, policy checks, dashboard API. |
| `tiresias-enterprise` | Commercial | BYOK key providers, Aletheia chain-of-thought auditing, air-gap licence relay, SIEM integration, MSSP and white-label. Not in this repository. |

## Links

- [QUICKSTART.md](./QUICKSTART.md): the older Docker quickstart, with its own correction note
- [ROADMAP.md](./ROADMAP.md): planned work for the Tiresias product
- [SECURITY.md](./SECURITY.md): how to report a vulnerability
- [Zero Trust for AI Agents](https://www.saluca.com/p/zero-trust-for-ai-agents): why agents need their own security model
- [Your AI Agent Forgets Everything](https://www.saluca.com/p/your-ai-agent-forgets-everything): the case for persistent agent identity
- [We've Been Chasing the Wrong Intelligence](https://www.saluca.com/p/weve-been-chasing-the-wrong-intelligence): governance over capability
- Enterprise: [tiresias.network](https://tiresias.network); contact enterprise@saluca.com for enterprise licensing, BYOK, air-gap deployments and partner programs.
- [llms.txt](./llms.txt): a link map of this repo for AI agents

_Last updated: 2026-10-22_
