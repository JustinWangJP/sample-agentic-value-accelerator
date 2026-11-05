"""過去に見つかった依存方向の違反を検査で拒否する。"""

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_imports.py"
SPEC = importlib.util.spec_from_file_location("check_imports", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


@pytest.mark.parametrize(
    "source",
    [
        "from registry_portal.errors import DiscoveryUnavailableError",
        "import registry_portal.errors as errors",
        "from registry_portal import errors",
        "from ..errors import DiscoveryUnavailableError",
        "from .. import errors",
    ],
)
def test_service_rejects_api_errors(tmp_path: Path, source: str) -> None:
    path = tmp_path / "src/registry_portal/services/example.py"
    path.parent.mkdir(parents=True)
    path.write_text(source)
    assert "サービス層は API エラーに依存できません" in CHECKER.violations(path, tmp_path)[0]


@pytest.mark.parametrize(
    "source",
    [
        "from test_catalog_list import make_handler",
        "import test_catalog_list as helpers",
        "from tests import test_catalog_list",
        "from .test_catalog_list import make_handler",
        "from . import test_catalog_list",
    ],
)
def test_tests_reject_other_test_modules(tmp_path: Path, source: str) -> None:
    path = tmp_path / "tests/test_example.py"
    path.parent.mkdir(parents=True)
    (path.parent / "test_catalog_list.py").write_text("")
    path.write_text(source)
    assert "テストモジュール間の import はできません" in CHECKER.violations(path, tmp_path)[0]


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("src/registry_portal/services/example.py", "from ..domain.catalog import CatalogItem"),
        ("src/registry_portal/catalog.py", "from .errors import DiscoveryUnavailableError"),
        ("tests/test_example.py", "from catalog_helpers import make_handler"),
        ("tests/test_example.py", "import pytest"),
        ("tests/test_example.py", "from helpers import test_data"),
        ("tests/test_example.py", "from .helpers import test_client"),
    ],
)
def test_allowed_imports_pass(tmp_path: Path, relative: str, source: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(source)
    assert CHECKER.violations(path, tmp_path) == []
