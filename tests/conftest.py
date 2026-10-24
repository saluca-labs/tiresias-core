from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from tiresias.config import TiresiasSettings
from tiresias.storage.engine import close_all_engines


FIXED_TENANT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


@pytest.fixture
def tmp_data_root(tmp_path: Path) -> Path:
    """Temporary data root directory for tests."""
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    return data_root


@pytest.fixture
def sample_tenant_id() -> str:
    """Fixed tenant UUID for test determinism."""
    return FIXED_TENANT_ID


@pytest.fixture
def test_settings(tmp_data_root: Path, sample_tenant_id: str) -> TiresiasSettings:
    """TiresiasSettings configured for testing."""
    return TiresiasSettings(
        TIRESIAS_TENANT_ID=sample_tenant_id,
        TIRESIAS_DATA_ROOT=tmp_data_root,
    )


@pytest.fixture(autouse=True)
async def cleanup_engines():
    """Ensure all SQLAlchemy engines are disposed after each test."""
    yield
    await close_all_engines()


_KNOWN_FAILURES_FILE = Path(__file__).with_name("known_failures.txt")


def pytest_collection_modifyitems(config, items):
    """Mark tests listed in known_failures.txt as strict xfail (see that file)."""
    known = {
        line.strip()
        for line in _KNOWN_FAILURES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for item in items:
        if item.nodeid.replace("\\", "/") in known:
            item.add_marker(
                pytest.mark.xfail(strict=True, reason="pre-existing failure, see tests/known_failures.txt")
            )


class _TTY:
    """A throwaway terminal for first_boot() to show the key on."""

    def write(self, _s: str) -> int:
        return 0

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True


async def provision_tenant_key(settings: TiresiasSettings) -> str:
    """Run first_boot for `settings` and return the tenant's API key.

    Single-tenant proxies require that key on every route, so app-level tests
    provision it before the app's own lifespan runs (first_boot is idempotent,
    so the lifespan then finds the tenant initialised).
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from tiresias.bootstrap import first_boot
    from tiresias.storage.engine import get_engine

    engine = await get_engine(settings.tenant_id, settings.data_root)
    async with AsyncSession(engine) as session:
        key = await first_boot(settings.tenant_id, settings, session, out=_TTY())
    assert key, "tenant was already initialised; use a fresh data root"
    return key
