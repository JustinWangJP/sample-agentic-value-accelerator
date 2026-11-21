"""本体スタックが作る Cognito グループ名が、バックエンドの認可が読むグループ名と一致することを確かめる。

グループ名は Terraform（infrastructure/main.tf）と Python（backend/src/registry_portal/auth.py・
domain/teams.py）の両方に現れ、言語をまたいで定数を共有できない。どちらかだけを改名すると、
ログインできても業務ロールや所属チームが付かなくなる。
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend/src/registry_portal"


class CognitoGroupTests(unittest.TestCase):
    def test_business_role_groups_match_the_backend_authorization(self):
        main = (ROOT / "infrastructure/main.tf").read_text()
        block = re.search(
            r'resource "aws_cognito_user_group" "business" \{\s*for_each\s*=\s*toset\(\[([^\]]+)\]\)',
            main,
        )
        self.assertIsNotNone(block)
        terraform_groups = re.findall(r'"([^"]+)"', block.group(1))
        auth = (BACKEND / "auth.py").read_text()
        role_groups = re.search(r"_ROLE_GROUPS: .*? = \((.*?)\n\)", auth, re.DOTALL)
        self.assertIsNotNone(role_groups)
        backend_groups = re.findall(r'\(Role\.\w+, "([^"]+)"\)', role_groups.group(1))
        self.assertTrue(backend_groups)
        self.assertEqual(sorted(terraform_groups), sorted(backend_groups))

    def test_team_group_prefix_matches_the_backend(self):
        main = (ROOT / "infrastructure/main.tf").read_text()
        team = re.search(
            r'resource "aws_cognito_user_group" "team" \{[^}]*name\s*=\s*"([^"$]*)\$\{each\.value\}"',
            main,
        )
        self.assertIsNotNone(team)
        teams = (BACKEND / "domain/teams.py").read_text()
        prefix = re.search(r'^TEAM_GROUP_PREFIX(?:: \w+)? = "([^"]+)"', teams, re.MULTILINE)
        self.assertIsNotNone(prefix)
        self.assertEqual(team.group(1), prefix.group(1))


if __name__ == "__main__":
    unittest.main()
