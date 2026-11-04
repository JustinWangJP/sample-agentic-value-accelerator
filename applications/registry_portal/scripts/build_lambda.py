"""管理 Lambda に固定版 SDK とアプリケーションを同梱する。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/management.zip")
    args = parser.parse_args()
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
        for package in ("boto3", "botocore"):
            metadata = target / f"{package}-1.43.123.dist-info/METADATA"
            if not metadata.is_file():
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
