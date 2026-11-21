"""撤去用に手元で外す保護設定が、ソースに残っていることを確かめる。

lifecycle.prevent_destroy は Terraform の制約で変数にできない。dev を撤去するときは
手元で行を外して destroy し、その変更はコミットしない手順にしている（#53）。
この検査は、撤去用の編集が誤ってコミットされたことを検出する。
"""

from pathlib import Path
import re
import unittest

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


def has_setting(block: str, key: str, value: str) -> bool:
    """コメントを除いた本文に設定があるかを返す。撤去時は行をコメントアウトすることが多いため。"""
    code = re.sub(r"(#|//).*$", "", block, flags=re.MULTILINE)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    return re.search(rf"\b{key}\s*=\s*{value}\b", code) is not None


class TeardownGuardTests(unittest.TestCase):
    def test_auxiliary_table_keeps_prevent_destroy(self):
        block = resource_block("main.tf", "aws_dynamodb_table", "auxiliary")
        self.assertTrue(
            has_setting(block, "prevent_destroy", "true"),
            "補助テーブルの lifecycle { prevent_destroy = true } を戻してください。"
            "撤去のために外した変更はコミットしません。",
        )

    def test_state_bucket_keeps_prevent_destroy_and_no_force_destroy(self):
        block = resource_block("bootstrap/main.tf", "aws_s3_bucket", "state")
        self.assertTrue(
            has_setting(block, "prevent_destroy", "true"),
            "state バケットの lifecycle { prevent_destroy = true } を戻してください。"
            "state バケットは撤去の対象に含めません。",
        )
        self.assertTrue(
            has_setting(block, "force_destroy", "false"),
            "state バケットの force_destroy = false を戻してください。",
        )


if __name__ == "__main__":
    unittest.main()
