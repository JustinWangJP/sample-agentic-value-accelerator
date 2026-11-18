"""管理・決裁・イベント処理 Lambda 共通の ZIP に、固定版 SDK とアプリケーションを同梱する。

3 つの Lambda は同じ ZIP を使い、handler だけが違う。既定の出力先は追加スタック
（decision・notification_events）の tfvars 例が指す `backend/build/lambda.zip`。
"""

from __future__ import annotations

import argparse
from importlib.metadata import Distribution
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# Terraform の各 Lambda の handler（本体・modules/decision・modules/notification_events）
LAMBDA_HANDLER_MODULES = (
    "registry_portal.handlers.management",
    "registry_portal.handlers.curation",
    "registry_portal.handlers.events",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "backend/build/lambda.zip")
    args = parser.parse_args()
    for module in LAMBDA_HANDLER_MODULES:
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
