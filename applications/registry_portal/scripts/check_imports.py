"""Registry ポータルのサービス層とテストの import 境界を検査する。"""

import ast
from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1] / "backend"


def violations(path: Path, root: Path = BACKEND) -> list[str]:
    relative = path.relative_to(root)
    parts = relative.with_suffix("").parts
    package = parts[:-1]
    if package and package[0] == "src":
        package = package[1:]
    service = parts[:3] == ("src", "registry_portal", "services")
    test = parts[0] == "tests" and path.stem.startswith("test_")
    errors = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join((*package[: len(package) - node.level + 1], base)).rstrip(".")
            modules = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            if test:
                modules = [base]
                for alias in node.names:
                    imported = f"{base}.{alias.name}".lstrip(".")
                    local = imported.removeprefix("tests.").split(".")
                    candidate = root / "tests" / Path(*local)
                    if (
                        candidate.with_suffix(".py").is_file()
                        or (candidate / "__init__.py").is_file()
                    ):
                        modules.append(imported)
        for module in modules:
            if service and (
                module == "registry_portal.errors" or module.startswith("registry_portal.errors.")
            ):
                errors.append(
                    f"{relative}:{node.lineno}: サービス層は API エラーに依存できません。"
                    "サービスの例外を定義し、API 層で変換してください。"
                )
                break
            if test and any(part.startswith("test_") for part in module.split(".")):
                errors.append(
                    f"{relative}:{node.lineno}: テストモジュール間の import はできません。"
                    "共通処理はヘルパーか fixture に移してください。"
                )
                break
    return errors


def main() -> int:
    errors = [
        error
        for directory in (BACKEND / "src", BACKEND / "tests")
        for path in sorted(directory.rglob("*.py"))
        for error in violations(path)
    ]
    for error in errors:
        print(error, file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
