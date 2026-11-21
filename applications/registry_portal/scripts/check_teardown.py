"""dev の撤去（scripts/deploy.sh --destroy）で、Terraform を反映する前に行う検査。

addon-states <スタック名>=<terraform state pull の出力> ...
    本体を撤去する前に、追加スタックの state にリソースが残っていないことを確かめる。
    撤去の順番は「追加スタック → 本体」（ADR-0014）。

    state のファイルがないと terraform state pull は何も出力せず、終了コードは 0 になる。
    terraform show -json は、state がない場合とリソースのない state の場合に同じ
    {"format_version":"1.0"} を出力して区別できないため、state pull を使う。どちらの出力も
    公式ドキュメントに記載がなく、Terraform 1.17.1 で確認した。

main-protection <destroy の plan の JSON>
    本体の destroy の plan で、補助テーブルの削除保護と SPA バケットの force_destroy が、
    state 上（削除前の値）で外れていることを確かめる。force_destroy は state に入った値が
    destroy に効くため、保護を外す apply（ガイド 5.4 の手順 3）を飛ばすと、保護のない
    リソースだけを消してから途中で失敗する。
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, NamedTuple

ORDER_HINT = (
    "撤去の順番は「追加スタック → 本体」です。"
    "先に scripts/deploy.sh --destroy --stack <追加スタック> で撤去してください。"
)


# terraform state pull が出力する state の形式（format version）。ほかの形式は件数を数えずに止める
STATE_FORMAT_VERSION = 4


def managed_resource_count(pulled: str) -> int | None:
    """terraform state pull の出力から、インスタンスを持つ managed のリソースの件数を返す。

    state のファイルがない（出力が空）場合は None を返す。読めない場合は ValueError を送出する。
    """
    if not pulled.strip():
        return None
    state = json.loads(pulled)
    if state.get("version") != STATE_FORMAT_VERSION:
        raise ValueError(f"state の形式（version {state.get('version')}）に対応していません")
    resources = state.get("resources")
    if not isinstance(resources, list):
        raise ValueError("state に resources がありません")
    return sum(
        1
        for resource in resources
        if resource.get("mode") == "managed" and resource.get("instances")
    )


class Protection(NamedTuple):
    """撤去の前に外しておく保護の設定。値は destroy の plan の削除前の値（state の値）と比べる。"""

    address: str
    attribute: str
    teardown_value: bool


MAIN_PROTECTIONS = (
    Protection("aws_dynamodb_table.auxiliary", "deletion_protection_enabled", False),
    Protection("aws_s3_bucket.spa", "force_destroy", True),
)
GUIDE_STEP = (
    "保護を外す apply（docs/agents/registry-portal-terraform-guide-ja.md の 5.4 の手順 3）を"
    "済ませてから、撤去をやり直してください。"
)


def remaining_protections(plan: dict[str, Any]) -> list[str]:
    """destroy の plan で、state 上の保護が撤去用の値になっていないリソースを返す。

    plan に出ないリソース（撤去の途中で削除済み）は対象にしない。
    """
    if not str(plan.get("format_version", "")).startswith("1."):
        raise ValueError("対応する plan JSON の format_version がありません")
    before = {
        change["address"]: change["change"].get("before") or {}
        for change in plan.get("resource_changes", [])
        if "delete" in change["change"].get("actions", [])
    }
    errors = []
    for protection in MAIN_PROTECTIONS:
        if protection.address not in before:
            continue
        value = before[protection.address].get(protection.attribute)
        if value is not protection.teardown_value:
            errors.append(
                f"{protection.address} の {protection.attribute} が state 上で "
                f"{json.dumps(value)} です（撤去には {json.dumps(protection.teardown_value)} が必要）"
            )
    return errors


def check_main_protection(path: str) -> int:
    try:
        errors = remaining_protections(json.loads(Path(path).read_text()))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"本体の destroy の plan を読めません: {error}", file=sys.stderr)
        return 1
    if errors:
        print("\n".join([*errors, GUIDE_STEP]), file=sys.stderr)
        return 1
    print("本体の保護が state 上で外れていることを確認しました")
    return 0


def check_addon_states(arguments: list[str]) -> int:
    remaining = []
    for argument in arguments:
        stack, _, path = argument.partition("=")
        try:
            count = managed_resource_count(Path(path).read_text())
        except (OSError, ValueError, AttributeError) as error:
            print(f"{stack} スタックの state を読めません: {error}", file=sys.stderr)
            return 1
        if count is None:
            print(f"{stack}: state なし（未配信）")
            continue
        print(f"{stack}: {count} 件")
        if count:
            remaining.append(f"{stack}（{count} 件）")
    if remaining:
        print(
            f"追加スタックの state にリソースが残っています: {' '.join(remaining)}。{ORDER_HINT}",
            file=sys.stderr,
        )
        return 1
    return 0


USAGE = """使用方法:
  check_teardown.py addon-states <スタック名>=<state pull の出力> ...
  check_teardown.py main-protection <destroy の plan の JSON>"""


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "addon-states":
        return check_addon_states(sys.argv[2:])
    if len(sys.argv) == 3 and sys.argv[1] == "main-protection":
        return check_main_protection(sys.argv[2])
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
