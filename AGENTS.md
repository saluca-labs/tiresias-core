# AGENTS.md

Instructions for AI coding agents working in this repository.

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
- No em-dashes in docs.

## Do not

- Commit `.env` files, keys, licence tokens, tailnet or private IP addresses, internal hostnames or local paths. Defaults for service URLs stay empty or use public names.
- Remove a test to make the suite pass; fix it or add it to `tests/known_failures.txt` with the cause.

## Done means

`uv run --locked python -m pytest -q` passes, and `llms.txt` / `llms-full.txt` were updated if `README.md`, `QUICKSTART.md`, `SECURITY.md` or this file changed.
