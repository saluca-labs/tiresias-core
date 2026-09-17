"""The README and package metadata must state the licence the LICENSE file grants.

LICENSE is authoritative. This repo was relicensed to FSL-1.1-Apache and the
README kept advertising "Apache 2.0 core" for weeks afterwards.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_license_file_is_fsl():
    head = (ROOT / "LICENSE").read_text(encoding="utf-8")[:400]
    assert "Functional Source License, Version 1.1" in head


def test_pyproject_declares_fsl():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert meta["project"].get("license") == "LicenseRef-FSL-1.1-Apache-2.0"


def test_readme_does_not_claim_apache_core():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # "Apache 2.0" may only appear as the future licence FSL converts to,
    # or in rows describing other products.
    bad = re.findall(r"Apache[ -]2\.0 core|Open source core \| Apache", readme)
    assert not bad, f"README claims an Apache core: {bad}"
    assert "FSL-1.1-Apache" in readme


def test_readme_has_no_pypi_install_for_our_package():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert not re.search(r"pip install (tiresias-core|tiresias-sdk|tiresias)\b(?!@)", readme)
