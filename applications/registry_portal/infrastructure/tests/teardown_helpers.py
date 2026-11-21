"""撤去の検査のテストで共通に使うデータと関数。"""

from pathlib import Path
import sys
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

# 追加スタックの state に残るリソースの例。インスタンスを持つ managed のリソースとして数える
LOG_GROUP: dict[str, Any] = {
    "mode": "managed",
    "type": "aws_cloudwatch_log_group",
    "name": "this",
    "instances": [{"attributes": {}}],
}


def pulled_state(*resources: dict[str, Any]) -> dict[str, Any]:
    """terraform state pull が出力する state（format version 4）を返す。"""
    return {"version": 4, "serial": 1, "lineage": "x", "resources": list(resources)}


def main_protections() -> tuple[Any, ...]:
    """撤去の前に外す本体の保護（scripts/check_teardown.py の MAIN_PROTECTIONS）を返す。

    保護の対象の Python 側の情報源はこの定数だけにし、テストに同じ値を書き写さない。
    """
    sys.path.insert(0, str(SCRIPTS))
    try:
        import check_teardown
    finally:
        sys.path.pop(0)
    return check_teardown.MAIN_PROTECTIONS
