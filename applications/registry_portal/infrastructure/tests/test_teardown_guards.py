"""撤去用に手元で外す保護設定が、ソースに残っていることを確かめる。

lifecycle.prevent_destroy は Terraform の制約で変数にできない。dev を撤去するときは
手元で行を外して destroy し、その変更はコミットしない手順にしている（#53）。
この検査は、撤去用の編集が誤ってコミットされたことを検出する。
"""

from pathlib import Path
import re
import unittest

from teardown_helpers import main_protections

INFRA = Path(__file__).resolve().parents[1]


def resource_block(path: str, resource_type: str, name: str) -> str:
    """resource ブロックの本文を返す。ブロックは行頭の `}` で閉じる前提とする。"""
    source = (INFRA / path).read_text()
    match = re.search(
        rf'^resource "{resource_type}" "{name}" \{{\n(.*?)^\}}',
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"{path} に {resource_type}.{name} がありません"
    return match.group(1)


def nested_blocks(code: str, name: str) -> list[str]:
    """`name { ... }` の形の入れ子のブロックの本文を返す。中括弧の対応をたどって閉じ位置を決める。"""
    bodies = []
    for match in re.finditer(rf"\b{name}\s*\{{", code):
        depth = 1
        for index in range(match.end(), len(code)):
            depth += {"{": 1, "}": -1}.get(code[index], 0)
            if depth == 0:
                bodies.append(code[match.end() : index])
                break
    return bodies


def has_setting(block: str, key: str, value: str, within: str | None = None) -> bool:
    """コメントを除いた本文に設定があるかを返す。撤去時は行をコメントアウトすることが多いため。

    within にブロック名（lifecycle など）を指定すると、そのブロックの中にある設定だけを数える。
    """
    code = re.sub(r"(#|//).*$", "", block, flags=re.MULTILINE)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    scopes = [code] if within is None else nested_blocks(code, within)
    return any(re.search(rf"\b{key}\s*=\s*{value}\b", scope) for scope in scopes)


class TeardownGuardTests(unittest.TestCase):
    def test_auxiliary_table_keeps_prevent_destroy(self):
        block = resource_block("main.tf", "aws_dynamodb_table", "auxiliary")
        self.assertTrue(
            has_setting(block, "prevent_destroy", "true", within="lifecycle"),
            "補助テーブルの lifecycle { prevent_destroy = true } を戻してください。"
            "撤去のために外した変更はコミットしません。",
        )

    def test_state_bucket_keeps_prevent_destroy_and_no_force_destroy(self):
        block = resource_block("bootstrap/main.tf", "aws_s3_bucket", "state")
        self.assertTrue(
            has_setting(block, "prevent_destroy", "true", within="lifecycle"),
            "state バケットの lifecycle { prevent_destroy = true } を戻してください。"
            "state バケットは撤去の対象に含めません。",
        )
        self.assertTrue(
            has_setting(block, "force_destroy", "false"),
            "state バケットの force_destroy = false を戻してください。",
        )

    def test_main_protections_name_resources_and_attributes_in_the_source(self):
        # check_teardown.py は plan に出ないアドレスを削除済みとみなして検査を飛ばすため、
        # リソース名や属性名の変更に追従しないと、保護の検査が黙って働かなくなる
        sources = {path.name: path.read_text() for path in sorted(INFRA.glob("*.tf"))}
        for protection in main_protections():
            resource_type, name = protection.address.split(".")
            with self.subTest(address=protection.address):
                blocks = [
                    resource_block(path, resource_type, name)
                    for path, source in sources.items()
                    if f'resource "{resource_type}" "{name}" {{' in source
                ]
                self.assertEqual(
                    len(blocks), 1, f"{protection.address} が infrastructure/ にありません"
                )
                self.assertTrue(
                    has_setting(blocks[0], protection.attribute, rf"var\.{protection.variable}"),
                    f"{protection.attribute} = var.{protection.variable} がありません",
                )

    def test_tfvars_example_keeps_teardown_variables_commented_out(self):
        # 例をコピーした dev.tfvars で保護が外れないよう、撤去用の変数は既定値のまま（コメントアウト）にする
        lines = (INFRA / "terraform.tfvars.example").read_text().splitlines()
        for variable in (protection.variable for protection in main_protections()):
            with self.subTest(variable=variable):
                assignments = [
                    line for line in lines if re.match(rf"\s*(#\s*)?{variable}\s*=", line)
                ]
                self.assertTrue(assignments, f"tfvars の例に {variable} の説明がありません")
                for line in assignments:
                    self.assertRegex(
                        line,
                        rf"^\s*#\s*{variable}\s*=",
                        f"tfvars の例では {variable} をコメントアウトしたままにしてください",
                    )


class HasSettingTests(unittest.TestCase):
    """has_setting が、撤去のための編集（行の削除・コメントアウト・lifecycle の外への移動）を見分けること。"""

    def test_setting_inside_lifecycle_is_found(self):
        for block in (
            '  name = "t"\n  lifecycle { prevent_destroy = true }\n',
            "  lifecycle {\n    ignore_changes  = [tags]\n    prevent_destroy = true\n  }\n",
        ):
            with self.subTest(block=block):
                self.assertTrue(has_setting(block, "prevent_destroy", "true", within="lifecycle"))

    def test_setting_outside_lifecycle_or_commented_out_is_not_found(self):
        for block in (
            "  prevent_destroy = true\n  lifecycle { ignore_changes = [tags] }\n",
            "  lifecycle {\n    ignore_changes = [tags]\n  }\n  prevent_destroy = true\n",
            "  # lifecycle { prevent_destroy = true }\n",
            "  lifecycle {\n    # prevent_destroy = true\n  }\n",
            "  lifecycle {\n    /* prevent_destroy = true */\n  }\n",
            "  lifecycle { prevent_destroy = false }\n",
        ):
            with self.subTest(block=block):
                self.assertFalse(has_setting(block, "prevent_destroy", "true", within="lifecycle"))


if __name__ == "__main__":
    unittest.main()
