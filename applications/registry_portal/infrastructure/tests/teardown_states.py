"""撤去の検査のテスト（test_check_teardown.py・test_deploy.py）で共通に使う state のデータ。"""

from typing import Any

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
