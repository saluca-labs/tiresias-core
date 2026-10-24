"""Revert each fix in place, confirm its tests fail, restore. Run from repo root."""
import subprocess
import sys

T_HDR = "tests/test_proxy/test_upstream_headers.py"
T_AUTH = "tests/test_proxy/test_single_tenant_auth.py"
T_BOOT = "tests/test_bootstrap.py"
T_REV = "tests/test_proxy/test_review_regressions.py"
SHOW = "    _show_key_once(api_key, tenant_id, settings, out if out is not None else sys.stdout)\n"

MUTATIONS = [
    ("1a catch-all back to the old 3-header strip list", "src/tiresias/proxy/app.py",
     '''        # Allowlist, not a strip list: this route used to forward every header
        # except three, which sent x-tiresias-api-key to the upstream provider.
        upstream_headers = llm_upstream_headers(request.headers, request.state)
        body_bytes = await request.body()''',
     '''        upstream_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding")
        }
        body_bytes = await request.body()''',
     [T_HDR + "::test_catch_all_never_forwards_tiresias_key"]),
    ("1b chat path back to the old strip list", "src/tiresias/proxy/app.py",
     '''        upstream_headers = llm_upstream_headers(request.headers, request.state)

        if is_streaming:''',
     '''        upstream_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding", "x-tiresias-api-key")
        }

        if is_streaming:''',
     [T_HDR + "::test_streaming_chat_never_forwards_tiresias_key",
      T_HDR + "::test_routed_chat_sends_only_the_provider_credential"]),
    ("1c router back to a case-sensitive dict merge", "src/tiresias/providers/router.py",
     '''                merged_headers = merge_provider_headers(
                    strip_client_provider_credentials(extra_headers), provider_headers
                )''',
     '''                merged_headers = {**extra_headers, **provider_headers}''',
     [T_HDR + "::test_routed_chat_sends_only_the_provider_credential"]),
    ("1d generic proxy back to the old strip list", "src/tiresias/proxy/app.py",
     '''        upstream_headers = generic_upstream_headers(request.headers, request.state)''',
     '''        upstream_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding", "x-tiresias-api-key")
        }''',
     [T_HDR + "::test_generic_proxy_strips_tiresias_headers_keeps_api_headers"]),
    # Both defences off: the header-name check and the value check (4a).
    ("1e Authorization carrying the Tiresias key is forwarded", "src/tiresias/proxy/headers.py",
     [('''    if credential_header == "authorization":
        return True
''', ''),
      ('''        if _carries(v, cred_val):
            continue
        out[lk] = v
    return out


def generic_upstream_headers''', '''        out[lk] = v
    return out


def generic_upstream_headers''')],
     None,
     [T_HDR + "::test_catch_all_strips_authorization_when_it_carried_the_tiresias_key"]),
    ("2a first_boot logs the key again (old code)", "src/tiresias/bootstrap.py",
     '''    _show_key_once(api_key, tenant_id, settings, out if out is not None else sys.stdout)''',
     '''    border = "=" * 60
    logger.info(border)
    logger.info("TIRESIAS API KEY: %s", api_key)
    logger.info("Save this key -- it will not be shown again.")
    logger.info(border)''',
     [T_BOOT + "::test_first_boot_never_logs_api_key_on_tty",
      T_BOOT + "::test_first_boot_without_tty_writes_key_file_not_logs"]),
    ("2b redaction filter disabled", "src/tiresias/bootstrap.py",
     '''    redact_in_logs(api_key)
''', '''''',
     [T_BOOT + "::test_first_boot_key_is_redacted_if_logged_later"]),
    ("2c key printed on a non-TTY stream too", "src/tiresias/bootstrap.py",
     '''    if callable(isatty) and isatty():''', '''    if True:''',
     [T_BOOT + "::test_first_boot_without_tty_writes_key_file_not_logs"]),
    ("3a onprem auth back to a no-op (old code)", "src/tiresias/proxy/saas_auth.py",
     '''            return await self._single_tenant_auth(request, call_next)''',
     '''            return await call_next(request)''',
     [T_AUTH]),
    ("3b opt-out defaults on", "src/tiresias/config.py",
     '''    allow_unauthenticated: bool = Field(default=False,''',
     '''    allow_unauthenticated: bool = Field(default=True,''',
     [T_AUTH]),
    # Found by the independent review of PR #6.
    ("4a second copy of the key (other header) forwarded", "src/tiresias/proxy/headers.py",
     '''        if _carries(v, cred_val):
            continue
        out[lk] = v
    return out


def generic_upstream_headers''',
     '''        out[lk] = v
    return out


def generic_upstream_headers''',
     [T_REV + "::test_key_in_both_headers_never_forwarded",
      T_REV + "::test_key_in_duplicate_authorization_never_forwarded"]),
    ("4b router keeps the client's provider credential on failover", "src/tiresias/providers/router.py",
     '''                    strip_client_provider_credentials(extra_headers), provider_headers''',
     '''                    extra_headers, provider_headers''',
     [T_REV + "::test_failover_never_sends_client_credential_to_another_provider"]),
    ("4c key shown after the hash is stored", "src/tiresias/bootstrap.py",
     [(SHOW, ""), ("    return api_key\n", SHOW + "    return api_key\n")],
     None,
     [T_REV + "::test_failed_key_display_stores_nothing"]),
    ("4d tracebacks not redacted", "src/tiresias/bootstrap.py",
     '''        if record.exc_text:
            text = record.exc_text''',
     '''        if False:
            text = record.exc_text''',
     [T_REV + "::test_key_inside_traceback_is_redacted"]),
]

def _read(path):
    raw = open(path, "rb").read()
    return raw, raw.decode("utf-8").replace("\r\n", "\n"), b"\r\n" in raw


def _write(path, text, crlf):
    open(path, "wb").write((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


bad = 0
for name, path, fixed, reverted, tests in MUTATIONS:
    raw, src, crlf = _read(path)
    edits = fixed if isinstance(fixed, list) else [(fixed, reverted)]
    mutated = src
    for a, b in edits:
        assert mutated.count(a) == 1, (name, "anchor not found")
        mutated = mutated.replace(a, b)
    _write(path, mutated, crlf)
    try:
        r = subprocess.run(["uv", "run", "--locked", "python", "-m", "pytest", "-q", "-p", "no:warnings", "-p", "no:cacheprovider", *tests],
                           capture_output=True, text=True)
        tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]
        ok = r.returncode != 0
        bad += not ok
        print(f"{'KILLED ' if ok else 'SURVIVED'} {name}: {tail}")
    finally:
        open(path, "wb").write(raw)  # byte-for-byte restore, line endings included

r = subprocess.run(["uv", "run", "--locked", "python", "-m", "pytest", "-q", "-p", "no:warnings", "-p", "no:cacheprovider"], capture_output=True, text=True)
print("restored suite:", r.stdout.strip().splitlines()[-1])
sys.exit(1 if bad or r.returncode else 0)
