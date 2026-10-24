from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from tiresias.bootstrap import (
    first_boot,
    first_boot_key_path,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)
from tiresias.config import TiresiasSettings
from tiresias.storage.engine import get_engine
from tiresias.storage.schema import TiresiasLicense


async def get_session(tenant_id: str, data_root: Path) -> AsyncSession:
    engine = await get_engine(tenant_id, data_root)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return Session()


def test_generate_api_key_length():
    key = generate_api_key()
    assert isinstance(key, str)
    assert len(key) == 43


def test_generate_api_key_is_random():
    k1 = generate_api_key()
    k2 = generate_api_key()
    assert k1 != k2


def test_hash_api_key_returns_hex():
    key = generate_api_key()
    h = hash_api_key(key)
    assert isinstance(h, str)
    assert len(h) == 64  # SHA-256 hex = 64 chars
    int(h, 16)  # should not raise


def test_verify_api_key_correct():
    key = generate_api_key()
    h = hash_api_key(key)
    assert verify_api_key(key, h) is True


def test_verify_api_key_wrong():
    key = generate_api_key()
    h = hash_api_key(key)
    assert verify_api_key("wrong-key", h) is False


async def test_first_boot_returns_api_key(tmp_data_root: Path, sample_tenant_id: str):
    settings = TiresiasSettings(TIRESIAS_TENANT_ID=sample_tenant_id, TIRESIAS_DATA_ROOT=tmp_data_root)
    async with await get_session(sample_tenant_id, tmp_data_root) as session:
        api_key = await first_boot(sample_tenant_id, settings, session)
    assert api_key is not None
    assert len(api_key) == 43


async def test_first_boot_creates_license_row(tmp_data_root: Path, sample_tenant_id: str):
    settings = TiresiasSettings(TIRESIAS_TENANT_ID=sample_tenant_id, TIRESIAS_DATA_ROOT=tmp_data_root)
    async with await get_session(sample_tenant_id, tmp_data_root) as session:
        await first_boot(sample_tenant_id, settings, session)

    async with await get_session(sample_tenant_id, tmp_data_root) as session:
        stmt = select(TiresiasLicense).where(TiresiasLicense.tenant_id == sample_tenant_id)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
    assert row is not None
    assert row.wrapped_dek is not None


async def test_first_boot_idempotent(tmp_data_root: Path, sample_tenant_id: str):
    settings = TiresiasSettings(TIRESIAS_TENANT_ID=sample_tenant_id, TIRESIAS_DATA_ROOT=tmp_data_root)
    async with await get_session(sample_tenant_id, tmp_data_root) as session:
        key1 = await first_boot(sample_tenant_id, settings, session)
    async with await get_session(sample_tenant_id, tmp_data_root) as session:
        key2 = await first_boot(sample_tenant_id, settings, session)
    assert key1 is not None
    assert key2 is None


async def test_api_key_hash_stored_not_raw(tmp_data_root: Path, sample_tenant_id: str):
    settings = TiresiasSettings(TIRESIAS_TENANT_ID=sample_tenant_id, TIRESIAS_DATA_ROOT=tmp_data_root)
    async with await get_session(sample_tenant_id, tmp_data_root) as session:
        api_key = await first_boot(sample_tenant_id, settings, session)

    async with await get_session(sample_tenant_id, tmp_data_root) as session:
        stmt = select(TiresiasLicense).where(TiresiasLicense.tenant_id == sample_tenant_id)
        result = await session.execute(stmt)
        row = result.scalar_one()

    # The stored hash should not be the raw key
    assert row.api_key_hash != api_key
    # But verifying the raw key against the hash should succeed
    assert verify_api_key(api_key, row.api_key_hash) is True


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _records_text(records) -> str:
    """Everything a handler could write: formatted message, raw msg and args."""
    parts = []
    for r in records:
        parts.append(r.getMessage())
        parts.append(str(r.msg))
        parts.append(repr(r.args))
        if r.exc_text:
            parts.append(r.exc_text)
    return "\n".join(parts)


async def test_first_boot_never_logs_api_key_on_tty(
    tmp_data_root: Path, sample_tenant_id: str, caplog
):
    """The key reaches the terminal once and no log record, at any level."""
    settings = TiresiasSettings(TIRESIAS_TENANT_ID=sample_tenant_id, TIRESIAS_DATA_ROOT=tmp_data_root)
    out = _Stream(tty=True)
    with caplog.at_level(logging.DEBUG):
        async with await get_session(sample_tenant_id, tmp_data_root) as session:
            api_key = await first_boot(sample_tenant_id, settings, session, out=out)
    assert api_key
    assert caplog.records, "expected at least one log record, or this test proves nothing"
    assert api_key not in _records_text(caplog.records)
    assert out.getvalue().count(api_key) == 1
    assert not first_boot_key_path(settings, sample_tenant_id).exists()


async def test_first_boot_without_tty_writes_key_file_not_logs(
    tmp_data_root: Path, sample_tenant_id: str, caplog
):
    """No terminal: nothing on the stream, the key in a file, the log names the path only."""
    settings = TiresiasSettings(TIRESIAS_TENANT_ID=sample_tenant_id, TIRESIAS_DATA_ROOT=tmp_data_root)
    out = _Stream(tty=False)
    with caplog.at_level(logging.DEBUG):
        async with await get_session(sample_tenant_id, tmp_data_root) as session:
            api_key = await first_boot(sample_tenant_id, settings, session, out=out)
    assert api_key not in _records_text(caplog.records)
    assert api_key not in out.getvalue()
    path = first_boot_key_path(settings, sample_tenant_id)
    assert path.read_text(encoding="utf-8").strip() == api_key
    assert str(path) in caplog.text


async def test_first_boot_key_is_redacted_if_logged_later(
    tmp_data_root: Path, sample_tenant_id: str, caplog
):
    """A later log line that contains the key is redacted, not written."""
    settings = TiresiasSettings(TIRESIAS_TENANT_ID=sample_tenant_id, TIRESIAS_DATA_ROOT=tmp_data_root)
    with caplog.at_level(logging.DEBUG):
        async with await get_session(sample_tenant_id, tmp_data_root) as session:
            api_key = await first_boot(sample_tenant_id, settings, session, out=_Stream(tty=True))
        caplog.clear()
        logging.getLogger("tiresias.somewhere").error("upstream said: %s", api_key)
    assert caplog.records
    assert api_key not in _records_text(caplog.records)
    assert "[REDACTED]" in caplog.text


def test_port_config_env_override():
    settings = TiresiasSettings(PROXY_PORT=9090, DASHBOARD_PORT=4000)
    assert settings.proxy_port == 9090
    assert settings.dashboard_port == 4000
