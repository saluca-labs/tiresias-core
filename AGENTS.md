# AGENTS.md

Instructions for AI coding agents working in this repository.

## What this is

The source-available core of Tiresias: a self-hosted, OpenAI-compatible LLM proxy (Python, FastAPI) that routes non-streaming chat across providers, envelope-encrypts the prompt and completion it records, and tracks tokens and cost per session, with a dashboard API over the stored data. Policy enforcement, anomaly detection, identity issuance and the hash-chained audit log are not in it.

## Read in this order

1. [README.md](README.md): the correction note in Status, "How it works", and the routes and authentication tables in Usage.
2. [src/tiresias/proxy/app.py](src/tiresias/proxy/app.py): `create_app()`, every route, and the streaming and non-streaming handlers.
3. [src/tiresias/proxy/interceptor.py](src/tiresias/proxy/interceptor.py): what `record_turn()` stores, encrypts, hashes and sends out.
4. [src/tiresias/config.py](src/tiresias/config.py): every setting (`TiresiasSettings`).
5. [tests/known_failures.txt](tests/known_failures.txt): the tests that fail on `main` and why.

## What you can and cannot see

- You can see the proxy, its provider clients, encryption, storage and dashboard router.
- Several modules are in `src/` but not used by the running proxy: `routers/auth.py` (never mounted), and through it `auth/pdp.py`, `policy/loader.py`, `audit/logger.py` and `policies/`; `auth/rate_limiter.py` and `auth/spend_tracker.py` (imported by nothing); `dashboard/app.py`'s standalone factory. Do not describe them as features of the proxy.
- SoulAuth (`SOULAUTH_URL`) and SoulWatch (`SOULWATCH_URL`) are external services, not in this repository.
- Tiresias Enterprise (BYOK KEK providers, Aletheia, the licence relay, SIEM) is closed. `Dockerfile.relay`, which `docker-compose.yml` references, does not exist here.
- Another branch is working on security fixes in `src/`, `tests/`, `scripts/` and `.env.example`. Authentication described in the README is as on `origin/main` at 3c634b6; read the code on the branch you are on before relying on it.

## Using it as an agent

```bash
uv sync --locked --extra dev
export TIRESIAS_KEK=$(python -c "import secrets; print(secrets.token_hex(32))")
export TIRESIAS_DATA_ROOT=./data TIRESIAS_TENANT_ID=demo OPENAI_API_KEY=...
uv run uvicorn tiresias.proxy.app:app --port 8080
curl http://127.0.0.1:8080/health
```

Then point any OpenAI client at `http://127.0.0.1:8080/v1`. Use non-streaming requests if you want the proxy's provider keys and cascade; a streaming request without a provider prefix on the model is forwarded to `TIRESIAS_UPSTREAM_URL` with your own `Authorization` header.

## The only numbers to trust

| Number | Value | Source |
|---|---|---|
| Package version | 1.0.0 | `pyproject.toml` (the FastAPI app reports `0.6.0`, set in `create_app()`) |
| Tests | 295 passed, 40 xfailed on 2026-10-22 | `uv run --locked python -m pytest -q`; the xfails are listed in `tests/known_failures.txt` |
| Routes | 21 paths plus FastAPI's docs routes | `app.openapi()` on `create_app()`; listed in README "Usage" |
| Environment variables | the aliases in `TiresiasSettings`, plus `SOULAUTH_URL`, `SOULWATCH_URL`, `SOULWATCH_INTERNAL_API_KEY` and the provider keys | `src/tiresias/config.py`, `dashboard/auth.py`, `proxy/interceptor.py`, `providers/__init__.py` |
| Default retention | 30 days for turns, 90 for usage buckets | `retention_days`, `usage_retention_days` in `config.py` |
| SaaS key cache | 60 seconds | `_CACHE_TTL_SECONDS` in `proxy/saas_auth.py` |
| SoulWatch call timeout | 2 seconds | `_report_tool_invocations()` in `proxy/interceptor.py` |
| Python | 3.11+ declared; CI on 3.11 and 3.12 | `pyproject.toml`, `.github/workflows/test.yml` |

No throughput, latency or detection figure has a source in this repository; do not state one.

## Setup

```bash
uv sync --locked --extra dev
```

Python 3.11 or later; CI runs 3.11 and 3.12 on Ubuntu.

## Test

```bash
uv run --locked python -m pytest -q
```

Passing looks like `295 passed, 40 xfailed` (counts as of 2026-10-22). Tests listed in `tests/known_failures.txt` are marked `xfail(strict=True)` by `tests/conftest.py`: if you fix one, it starts passing, the strict xfail turns the run red, and you must remove it from that file.

## Conventions

- Configuration is environment variables declared in `src/tiresias/config.py`. Add new settings there, with an alias, rather than reading `os.environ` ad hoc.
- `uv.lock` is checked by CI (`--locked`); update it with `uv lock` when `pyproject.toml` changes.
- `LICENSE` is authoritative. `tests/test_licence_consistency.py` checks that README and `pyproject.toml` agree with it; keep the string `FSL-1.1-Apache` in the README and do not add a `pip install tiresias-core` line (the package is not on PyPI).
- Core must not claim enterprise features. If a doc mentions something that lives in Tiresias Enterprise or the platform, say so.
- The README diagrams are Mermaid source traced from `proxy/app.py` and `proxy/interceptor.py`. If you change a route or the recording path, change them in the same PR.
- No em-dashes in docs.

## Do not

- Commit `.env` files, keys, licence tokens, tailnet or private IP addresses, internal hostnames or local paths. Defaults for service URLs stay empty or use public names.
- Remove a test to make the suite pass; fix it or add it to `tests/known_failures.txt` with the cause.
- Describe the policy modules, `evaluate-sop` or the unused rate limiter as active until they are wired into `create_app()`.

## Done means

`uv run --locked python -m pytest -q` passes, and `llms.txt` / `llms-full.txt` were updated if `README.md`, `QUICKSTART.md`, `SECURITY.md` or this file changed.
