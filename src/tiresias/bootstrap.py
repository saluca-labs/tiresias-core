from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import TextIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiresias.config import TiresiasSettings
from tiresias.encryption.envelope import EnvelopeEncryption
from tiresias.encryption.providers import resolve_kek_provider
from tiresias.storage.schema import TiresiasLicense

logger = logging.getLogger(__name__)


class SecretRedactingFilter(logging.Filter):
    """Replace registered secret values in a log record with [REDACTED].

    Defence in depth. first_boot() never logs the key; this filter makes sure
    a later mistake (an exception message, a debug line, a third-party logger)
    cannot write it to a handler either.
    """

    def __init__(self) -> None:
        super().__init__()
        self._secrets: set[str] = set()

    def add(self, secret: str) -> None:
        if secret:
            self._secrets.add(secret)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        hit = False
        for secret in self._secrets:
            if secret in msg:
                msg = msg.replace(secret, "[REDACTED]")
                hit = True
        if hit:
            record.msg = msg
            record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            text = record.exc_text
            for secret in self._secrets:
                text = text.replace(secret, "[REDACTED]")
            if text != record.exc_text:
                record.exc_text = text
                record.exc_info = None
        return True


_REDACTOR = SecretRedactingFilter()

# Loggers whose handlers do not propagate to root under uvicorn.
_HANDLER_LOGGERS = ("", "tiresias", "uvicorn", "uvicorn.error", "uvicorn.access")


def redact_in_logs(secret: str) -> None:
    """Register `secret` for redaction on every currently attached handler."""
    _REDACTOR.add(secret)
    for name in _HANDLER_LOGGERS:
        lg = logging.getLogger(name)
        if _REDACTOR not in lg.filters:
            lg.addFilter(_REDACTOR)
        for handler in lg.handlers:
            if _REDACTOR not in handler.filters:
                handler.addFilter(_REDACTOR)


def first_boot_key_path(settings: TiresiasSettings, tenant_id: str) -> Path:
    return Path(settings.data_root) / "tenants" / tenant_id / "first-boot-api-key"


def _write_key_file(path: Path, api_key: str) -> Path:
    """Write the key to a new 0600 file. Never overwrites. Returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # A stale file from an earlier install: never overwrite a key someone
        # may not have read yet.
        path = path.with_name(f"{path.name}.{secrets.token_hex(4)}")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(api_key + "\n")
    return path


def _show_key_once(api_key: str, tenant_id: str, settings: TiresiasSettings,
                   out: TextIO) -> None:
    """Show the key once on an interactive terminal; never through logging.

    On a TTY the key goes to the terminal only. Without one (a container whose
    stdout is collected into logs, a service manager) printing it would put it
    in a log, so it is written to a 0600 file under the data root instead and
    the log names the path, never the value.
    """
    isatty = getattr(out, "isatty", None)
    if callable(isatty) and isatty():
        border = "=" * 60
        out.write(
            f"{border}\nTIRESIAS API KEY: {api_key}\n"
            "Save this key -- it will not be shown again.\n"
            f"{border}\n"
        )
        out.flush()
        logger.info("First-boot API key shown once on the terminal (not logged).")
        return
    path = _write_key_file(first_boot_key_path(settings, tenant_id), api_key)
    logger.warning(
        "No terminal attached: the first-boot API key was written to %s "
        "(mode 0600). Read it, store it, then delete the file.", path
    )


def generate_api_key() -> str:
    """Generate a URL-safe random API key (43 characters)."""
    return secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    """Return SHA-256 hex digest of the API key for storage."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def verify_api_key(provided: str, stored_hash: str) -> bool:
    """Constant-time comparison of provided API key against stored hash."""
    provided_hash = hash_api_key(provided)
    return hmac.compare_digest(provided_hash, stored_hash)


async def first_boot(
    tenant_id: str,
    settings: TiresiasSettings,
    session: AsyncSession,
    *,
    out: TextIO | None = None,
) -> str | None:
    """Initialize a tenant on first boot.

    - If a license row already exists for this tenant, returns None (no-op).
    - Otherwise: generates API key, resolves KEK provider, wraps DEK, creates license row.
    - Shows the API key once: on the terminal if `out` (default stdout) is a
      TTY, otherwise in a 0600 file under the data root. It is never passed to
      a logger, and it is registered with the log redaction filter.

    Returns the plaintext API key on first boot, None on subsequent boots.
    """
    stmt = select(TiresiasLicense).where(TiresiasLicense.tenant_id == tenant_id)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is not None:
        logger.debug("Tenant %s already initialized -- skipping first boot.", tenant_id)
        return None

    # Generate API key (used for API auth, and optionally for local KEK derivation)
    api_key = generate_api_key()
    redact_in_logs(api_key)

    # Show the key once, never through logging, BEFORE anything is persisted.
    # It used to go to logger.info, which put it in every log handler, file
    # and aggregator. If showing it fails (read-only data root, say) nothing
    # has been written yet, so the next boot simply tries again; the other
    # order would store the hash of a key nobody ever saw and lock the tenant
    # out for good.
    _show_key_once(api_key, tenant_id, settings, out if out is not None else sys.stdout)

    # Resolve KEK provider using factory — handles all 5 provider types
    provider = resolve_kek_provider(settings, api_key=api_key)

    # Generate and wrap DEK, create license row
    envelope = EnvelopeEncryption(provider)
    await envelope.create_dek_for_tenant(tenant_id, session)

    # Update license row with API key hash and full metadata
    stmt2 = select(TiresiasLicense).where(TiresiasLicense.tenant_id == tenant_id)
    result2 = await session.execute(stmt2)
    license_row = result2.scalar_one()
    license_row.api_key_hash = hash_api_key(api_key)
    license_row.retention_days = settings.retention_days
    await session.commit()


    return api_key

