"""予算アラートの通知先メールアドレスが、リポジトリの tfvars に書かれていないことを確かめる。

通知先は個人情報なので、環境変数 TF_VAR_budget_notification_emails で渡す（Issue #61 の実装案 4）。
tfvars の例や dev の値に書くと、そのままコミットされてしまう。
"""

from pathlib import Path
import re
import unittest

INFRA = Path(__file__).resolve().parents[1]
# コメントではない行で budget_notification_emails に値を代入しているもの
ASSIGNMENT = re.compile(r"^\s*budget_notification_emails\s*=", re.MULTILINE)


def committed_tfvars() -> list[Path]:
    # *.tfvars は .gitignore の対象。コミットされるのは例と dev の値だけ
    return sorted([*INFRA.rglob("*.tfvars.example"), *INFRA.rglob("dev.tfvars")])


class BudgetEmailTests(unittest.TestCase):
    def test_tfvars_examples_are_found(self):
        self.assertIn(INFRA / "bootstrap/terraform.tfvars.example", committed_tfvars())

    def test_committed_tfvars_do_not_assign_notification_emails(self):
        for path in committed_tfvars():
            with self.subTest(path=str(path.relative_to(INFRA))):
                self.assertIsNone(ASSIGNMENT.search(path.read_text()))


if __name__ == "__main__":
    unittest.main()
