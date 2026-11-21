"""dev の撤去（scripts/deploy.sh --destroy）で、Terraform を反映する前に行う検査。

addon-states <スタック名>=<terraform state pull の出力> ...
    本体を撤去する前に、追加スタックの state にリソースが残っていないことを確かめる。
    撤去の順番は「追加スタック → 本体」（ADR-0014）。

    state のファイルがないと terraform state pull は何も出力せず、終了コードは 0 になる。
    terraform show -json は、state がない場合とリソースのない state の場合に同じ
    {"format_version":"1.0"} を出力して区別できないため、state pull を使う。どちらの出力も
    公式ドキュメントに記載がなく、Terraform 1.17.1 で確認した。
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

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


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "addon-states":
        print(
            "使用方法: check_teardown.py addon-states <スタック名>=<state pull の出力> ...",
            file=sys.stderr,
        )
        return 2
    return check_addon_states(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
