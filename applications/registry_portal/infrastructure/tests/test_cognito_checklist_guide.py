"""Terraform ガイドの Cognito の確認項目（3.7）が、AWS の API の項目名と読み取りだけの確認方法を保つことを確かめる。

期待する項目名は AWS の API リファレンス（DescribeUserPool・UserPoolType・AdminGetUser）から取った。
"""

from pathlib import Path
import re
import unittest

REPO = Path(__file__).resolve().parents[4]
GUIDE = REPO / "docs/agents/registry-portal-terraform-guide-ja.md"
COGNITO_HEADING = "### 3.7 Cognito User Pool の設定を確認する"
API_REFERENCE = "https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/"


def cognito_section() -> str:
    """ガイドの Cognito の確認項目の節（次の見出しまで）を返す。"""
    guide = GUIDE.read_text()
    start = guide.find(f"\n{COGNITO_HEADING}\n")
    assert start >= 0, f"ガイドに「{COGNITO_HEADING}」の節がありません"
    end = re.search(r"\n#{2,3} ", guide[start + 1 :])
    return guide[start : start + 1 + end.start() if end else len(guide)]


def aws_commands(section: str) -> list[str]:
    """節の bash のコードブロックにある aws コマンドを、行の継続をつないで返す。

    `$(aws ...)` のようにコマンド置換の中にあるものも含める。
    """
    blocks = re.findall(r"^```bash\n(.*?)^```", section, re.DOTALL | re.MULTILINE)
    text = "\n".join(blocks).replace("\\\n", " ")
    return [" ".join(found.split()) for found in re.findall(r"\baws [^)\n]*", text)]


def query_of(command: str) -> str:
    match = re.search(r"--query '([^']+)'", command)
    assert match, f"--query がありません: {command}"
    return match.group(1)


class CognitoChecklistGuideTests(unittest.TestCase):
    def test_describe_user_pool_queries_self_signup_mfa_and_feature_plan(self):
        commands = [c for c in aws_commands(cognito_section()) if " describe-user-pool " in c]
        self.assertTrue(commands, "describe-user-pool の確認コマンドがありません")
        query = " ".join(query_of(c) for c in commands)
        for field in (
            "AdminCreateUserConfig.AllowAdminCreateUserOnly",
            "MfaConfiguration",
            "UserPoolTier",
        ):
            with self.subTest(field=field):
                self.assertRegex(query, rf"[:{{,]{re.escape(field)}\b")
        self.assertTrue(query_of(commands[0]).startswith("UserPool."))

    def test_user_mfa_is_checked_with_admin_get_user(self):
        commands = [c for c in aws_commands(cognito_section()) if " admin-get-user " in c]
        self.assertTrue(commands, "ユーザーごとの MFA を確かめるコマンドがありません")
        self.assertIn("UserMFASettingList", query_of(commands[0]))

    def test_checklist_commands_only_read(self):
        commands = aws_commands(cognito_section())
        self.assertTrue(commands)
        for command in commands:
            with self.subTest(command=command):
                operation = command.split()[2]
                self.assertRegex(operation, r"^(describe|list|get|admin-get)-")
                self.assertIn("--query", command, "出力を必要な項目に絞ってください")

    def test_section_cites_aws_api_reference_for_each_field(self):
        section = cognito_section()
        for page in (
            "API_DescribeUserPool.html",
            "API_AdminCreateUserConfigType.html",
            "API_UserPoolType.html",
            "API_AdminGetUser.html",
        ):
            with self.subTest(page=page):
                self.assertIn(API_REFERENCE + page, section)
        self.assertIn(
            "https://docs.aws.amazon.com/cognito/latest/developerguide/"
            "cognito-sign-in-feature-plans.html",
            section,
        )


if __name__ == "__main__":
    unittest.main()
