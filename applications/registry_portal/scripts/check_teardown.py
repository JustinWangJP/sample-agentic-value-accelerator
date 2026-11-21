"""dev の撤去（scripts/deploy.sh --destroy）で、Terraform を反映する前に行う検査。

addon-backends <本体の backend.hcl> <スタック名>=<追加スタックの backend.hcl> ...
    本体の撤去では、本体の backend.hcl の key だけを追加スタックのものに差し替えて、
    追加スタックの state を読む（同じバケットで key だけが異なる前提。ガイド 4.5）。
    その前に、手元にある追加スタックの backend.hcl の bucket と region が本体と一致する
    ことを確かめる。ファイルがない場合は、前提のとおりに読むことを表示する。

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
import re
import sys
from typing import Any, NamedTuple


class Protection(NamedTuple):
    """撤去の前に外しておく保護の設定。値は destroy の plan の削除前の値（state の値）と比べる。

    variable は、属性に渡す変数（variables.tf）の名前。撤去のときは tfvars でこの変数に
    teardown_value を指定して apply する（ガイド 5.4 の手順 3）。
    """

    address: str
    attribute: str
    variable: str
    teardown_value: bool


# 撤去の保護の対象の、Python 側の唯一の情報源。テストもここから値を取る
MAIN_PROTECTIONS = (
    Protection(
        "aws_dynamodb_table.auxiliary",
        "deletion_protection_enabled",
        "table_deletion_protection",
        False,
    ),
    Protection("aws_s3_bucket.spa", "force_destroy", "spa_force_destroy", True),
)
# terraform state pull が出力する state の形式（format version）。ほかの形式は件数を数えずに止める
STATE_FORMAT_VERSION = 4
ORDER_HINT = (
    "撤去の順番は「追加スタック → 本体」です。"
    "先に scripts/deploy.sh --destroy --stack <追加スタック> で撤去してください。"
)
GUIDE_STEP = (
    "保護を外す apply（docs/agents/registry-portal-terraform-guide-ja.md の 5.4 の手順 3）を"
    "済ませてから、撤去をやり直してください。"
)
# 本体と追加スタックの backend.hcl で一致を確かめる設定。state の場所を決める値
SHARED_BACKEND_SETTINGS = ("bucket", "region")
USAGE = """使用方法:
  check_teardown.py addon-backends <本体の backend.hcl> <スタック名>=<backend.hcl> ...
  check_teardown.py addon-states <スタック名>=<state pull の出力> ...
  check_teardown.py main-protection <destroy の plan の JSON>"""


def _managed_resource_count(pulled: str) -> int | None:
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


def _remaining_protections(plan: dict[str, Any]) -> tuple[list[str], list[Protection]]:
    """destroy の plan で、state 上の保護が撤去用の値になっていないリソースと、検査を飛ばした保護を返す。

    plan に出ないリソースは、撤去の途中で削除済みとみなして検査を飛ばす（途中で失敗した撤去を
    続けるため）。リソース名の変更でも plan に出なくなるため、飛ばした保護は呼び出し元で表示する。
    """
    if not str(plan.get("format_version", "")).startswith("1."):
        raise ValueError("対応する plan JSON の format_version がありません")
    before = {
        change["address"]: change["change"].get("before") or {}
        for change in plan.get("resource_changes", [])
        if "delete" in change["change"].get("actions", [])
    }
    errors = []
    skipped = []
    for protection in MAIN_PROTECTIONS:
        if protection.address not in before:
            skipped.append(protection)
            continue
        value = before[protection.address].get(protection.attribute)
        if value is not protection.teardown_value:
            errors.append(
                f"{protection.address} の {protection.attribute} が state 上で "
                f"{json.dumps(value)} です（撤去には {json.dumps(protection.teardown_value)} が必要）"
            )
    return errors, skipped


def check_main_protection(path: str) -> int:
    """destroy の plan の JSON を読み、本体の保護が state 上で外れていれば 0 を返す。"""
    try:
        errors, skipped = _remaining_protections(json.loads(Path(path).read_text()))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"本体の destroy の plan を読めません: {error}", file=sys.stderr)
        return 1
    for protection in skipped:
        print(
            f"{protection.address} は plan にないため、削除済みとみなして "
            f"{protection.attribute} の検査を飛ばしました"
        )
    if errors:
        print("\n".join([*errors, GUIDE_STEP]), file=sys.stderr)
        return 1
    print("本体の保護が state 上で外れていることを確認しました")
    return 0


def _stack_paths(arguments: list[str], path_label: str) -> list[tuple[str, str]] | None:
    """`<スタック名>=<パス>` の引数を (スタック名, パス) に分ける。

    `=` がない、またはスタック名かパスが空の引数があれば、その旨を表示して None を返す。
    path_label は、表示する引数の形でパスの部分に書く名前。
    """
    pairs = []
    for argument in arguments:
        stack, separator, path = argument.partition("=")
        if not (separator and stack and path):
            print(
                f"引数「{argument}」は <スタック名>=<{path_label}> の形で指定してください。",
                file=sys.stderr,
            )
            return None
        pairs.append((stack, path))
    return pairs


def check_addon_states(arguments: list[str]) -> int:
    """`<スタック名>=<state pull の出力>` の各 state にリソースが残っていなければ 0 を返す。

    引数の形が正しくなければ、どの state も読まずに 2 を返す。
    """
    pairs = _stack_paths(arguments, "state pull の出力")
    if pairs is None:
        return 2
    remaining = []
    for stack, path in pairs:
        try:
            count = _managed_resource_count(Path(path).read_text())
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


def _backend_settings(text: str) -> dict[str, str]:
    """backend.hcl の `名前 = "値"` の行を読む。コメントの行と、文字列でない値は読まない。"""
    return dict(re.findall(r'^\s*(\w+)\s*=\s*"([^"]*)"', text, re.MULTILINE))


def check_addon_backends(main_backend: str, arguments: list[str]) -> int:
    """手元の追加スタックの backend.hcl が、本体と同じ bucket と region を指していれば 0 を返す。

    ファイルがない追加スタックは、本体の bucket と region で key だけを差し替えて読むことを
    表示し、止めない。引数の形が正しくなければ 2 を返す。
    """
    pairs = _stack_paths(arguments, "backend.hcl")
    if pairs is None:
        return 2
    try:
        main = _backend_settings(Path(main_backend).read_text())
    except OSError as error:
        print(f"本体の backend.hcl を読めません: {error}", file=sys.stderr)
        return 1
    if not main.get("bucket"):
        print(f"本体の backend.hcl（{main_backend}）に bucket がありません", file=sys.stderr)
        return 1
    expected = {name: main.get(name) for name in SHARED_BACKEND_SETTINGS}
    mismatched = []
    for stack, path in pairs:
        try:
            text = Path(path).read_text()
        except FileNotFoundError:
            print(
                f"{stack}: {path} がないため、本体の backend.hcl のバケットとリージョンで"
                "、key だけを差し替えて state を読みます"
            )
            continue
        except OSError as error:
            print(f"{stack} スタックの backend.hcl を読めません: {error}", file=sys.stderr)
            return 1
        settings = _backend_settings(text)
        actual = {name: settings.get(name) for name in SHARED_BACKEND_SETTINGS}
        if actual != expected:
            mismatched.append(f"{stack}（{path}: {json.dumps(actual, ensure_ascii=False)}）")
            continue
        print(f"{stack}: backend.hcl のバケットとリージョンが本体と一致しました")
    if mismatched:
        print(
            f"追加スタックの backend.hcl のバケットかリージョンが、本体の backend.hcl と一致しません"
            f"（本体: {json.dumps(expected, ensure_ascii=False)}）: {' '.join(mismatched)}。"
            "本体の撤去は、追加スタックの state が本体と同じバケットにあり key だけが異なる前提で"
            "検査します（ガイド 4.5）。",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    """サブコマンドを実行し、終了コードを返す。引数が正しくなければ使い方を表示して 2 を返す。"""
    if len(sys.argv) >= 4 and sys.argv[1] == "addon-backends":
        return check_addon_backends(sys.argv[2], sys.argv[3:])
    if len(sys.argv) >= 3 and sys.argv[1] == "addon-states":
        return check_addon_states(sys.argv[2:])
    if len(sys.argv) == 3 and sys.argv[1] == "main-protection":
        return check_main_protection(sys.argv[2])
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
