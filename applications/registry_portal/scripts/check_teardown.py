"""dev の撤去（scripts/deploy.sh --destroy）で、Terraform を反映する前に行う検査。

addon-states <スタック名>=<state の JSON> ...
    本体を撤去する前に、追加スタックの state にリソースが残っていないことを確かめる。
    撤去の順番は「追加スタック → 本体」（ADR-0014）。
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ORDER_HINT = (
    "撤去の順番は「追加スタック → 本体」です。"
    "先に scripts/deploy.sh --destroy --stack <追加スタック> で撤去してください。"
)


def _managed_count(module: dict[str, Any]) -> int:
    own = sum(1 for resource in module.get("resources", []) if resource.get("mode") == "managed")
    return own + sum(_managed_count(child) for child in module.get("child_modules", []))


def managed_resource_count(state_json: str) -> int:
    """terraform show -json の state から、managed のリソースの件数を返す。"""
    state = json.loads(state_json)
    if not str(state.get("format_version", "")).startswith("1."):
        raise ValueError("state JSON の format_version がありません")
    return _managed_count(state.get("values", {}).get("root_module", {}))


def check_addon_states(arguments: list[str]) -> int:
    remaining = []
    for argument in arguments:
        stack, _, path = argument.partition("=")
        try:
            count = managed_resource_count(Path(path).read_text())
        except (OSError, ValueError, AttributeError) as error:
            print(f"{stack} スタックの state を読めません: {error}", file=sys.stderr)
            return 1
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
            "使用方法: check_teardown.py addon-states <スタック名>=<state の JSON> ...",
            file=sys.stderr,
        )
        return 2
    return check_addon_states(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
