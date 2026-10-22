"""Revert each fix in place, confirm its tests fail, restore. Run from repo root."""
import subprocess
import sys

T_HDR = "tests/test_proxy/test_upstream_headers.py"
T_AUTH = "tests/test_proxy/test_single_tenant_auth.py"
T_BOOT = "tests/test_bootstrap.py"

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
     '''                merged_headers = merge_provider_headers(extra_headers, provider_headers)''',
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
    ("1e Authorization carrying the Tiresias key is forwarded", "src/tiresias/proxy/headers.py",
     '''    if credential_header == "authorization":
        return True
''', '''''',
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
]

bad = 0
for name, path, fixed, reverted, tests in MUTATIONS:
    src = open(path, encoding="utf-8").read()
    assert src.count(fixed) == 1, (name, "anchor not found")
    open(path, "w", encoding="utf-8", newline="\n").write(src.replace(fixed, reverted))
    try:
        r = subprocess.run(["uv", "run", "--locked", "python", "-m", "pytest", "-q", "-p", "no:warnings", "-p", "no:cacheprovider", *tests],
                           capture_output=True, text=True)
        tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]
        ok = r.returncode != 0
        bad += not ok
        print(f"{'KILLED ' if ok else 'SURVIVED'} {name}: {tail}")
    finally:
        open(path, "w", encoding="utf-8", newline="\n").write(src)

r = subprocess.run(["uv", "run", "--locked", "python", "-m", "pytest", "-q", "-p", "no:warnings", "-p", "no:cacheprovider"], capture_output=True, text=True)
print("restored suite:", r.stdout.strip().splitlines()[-1])
sys.exit(1 if bad or r.returncode else 0)
