"""管理・決裁・イベント処理 Lambda 共通の ZIP に、固定版 SDK とアプリケーションを同梱する。

3 つの Lambda は同じ ZIP を使い、handler だけが違う。handler の一覧は Terraform の
Lambda 定義（handler 属性）から読み、ここでは持たない。既定の出力先は追加スタック
（decision・notification_events）の tfvars 例が指す `backend/build/lambda.zip`。
"""

from __future__ import annotations

import argparse
from importlib.metadata import Distribution
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# aws_lambda_function の handler 属性（"<モジュール>.<関数名>"）
_HANDLER_ATTRIBUTE = re.compile(r'^\s*handler\s*=\s*"([A-Za-z_][\w.]*)\.\w+"', re.MULTILINE)


def lambda_handler_modules(root: Path = ROOT) -> list[str]:
    """Terraform の Lambda 定義を正本として、共通 ZIP に必要な handler モジュールを返す。"""
    modules: set[str] = set()
    for definition in (root / "infrastructure").rglob("*.tf"):
        if ".terraform" not in definition.parts:
            modules.update(_HANDLER_ATTRIBUTE.findall(definition.read_text()))
    return sorted(modules)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "backend/build/lambda.zip")
    args = parser.parse_args()
    handler_modules = lambda_handler_modules()
    if not handler_modules:
        print("infrastructure/ に Lambda の handler の定義がありません", file=sys.stderr)
        return 1
    for module in handler_modules:
        if not (ROOT / "backend/src" / f"{module.replace('.', '/')}.py").is_file():
            print(f"Lambda の handler モジュールがありません: {module}", file=sys.stderr)
            return 1
    with tempfile.TemporaryDirectory(prefix="registry-portal-lambda-") as directory:
        target = Path(directory)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-compile",
                "--only-binary=:all:",
                "--platform",
                "manylinux2014_aarch64",
                "--implementation",
                "cp",
                "--python-version",
                "3.13",
                "--target",
                str(target),
                "-r",
                str(ROOT / "backend/requirements.txt"),
            ],
            check=True,
        )
        pinned_versions = dict(
            line.strip().split("==", 1)
            for line in (ROOT / "backend/requirements.txt").read_text().splitlines()
            if "==" in line
        )
        for package in ("boto3", "botocore"):
            version = pinned_versions.get(package)
            if not version:
                raise RuntimeError(f"SDK の固定バージョンがありません: {package}")
            metadata = target / f"{package}-{version}.dist-info/METADATA"
            if not metadata.is_file() or Distribution.at(metadata.parent).version != version:
                raise RuntimeError(f"固定版 SDK を同梱できませんでした: {package}")
        for service in ("agent-registry", "agent-registry-control"):
            if not (target / "botocore/data" / service).is_dir():
                raise RuntimeError(f"Registry SDK モデルがありません: {service}")
        shutil.copytree(
            ROOT / "backend/src/registry_portal",
            target / "registry_portal",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(target))
    print(json.dumps({"lambda_zip": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
