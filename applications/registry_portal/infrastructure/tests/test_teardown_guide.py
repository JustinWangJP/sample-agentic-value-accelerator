"""撤去手順書（Terraform ガイドの 5.4）が、変数・deploy.sh・保護の設定の実装と一致することを確かめる。"""

import os
from pathlib import Path
import re
import subprocess
import unittest

INFRA = Path(__file__).resolve().parents[1]
PORTAL = INFRA.parent
REPO = INFRA.parents[2]
GUIDE = REPO / "docs/agents/registry-portal-terraform-guide-ja.md"
TEARDOWN_HEADING = "### 5.4 dev 環境を撤去する"


def github_anchor(heading: str) -> str:
    """GitHub の見出しアンカーを返す。英字は小文字にし、記号を除き、空白をハイフンにする。"""
    text = heading.lstrip("#").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def teardown_section() -> str:
    """ガイドの撤去手順の節（次の章の見出しまで）を返す。"""
    guide = GUIDE.read_text()
    start = guide.find(f"\n{TEARDOWN_HEADING}\n")
    assert start >= 0, f"ガイドに「{TEARDOWN_HEADING}」の節がありません"
    end = guide.find("\n## ", start + 1)
    return guide[start : end if end >= 0 else len(guide)]


def code_blocks(text: str, language: str) -> list[str]:
    return re.findall(rf"^```{language}\n(.*?)^```", text, re.DOTALL | re.MULTILINE)


def deploy_usage() -> str:
    """引数なしで deploy.sh を実行し、使い方の表示を返す（外部コマンドは呼ばれない）。"""
    result = subprocess.run(
        [str(PORTAL / "scripts/deploy.sh")],
        text=True,
        capture_output=True,
        env={**os.environ, "CI": "", "GITHUB_ACTIONS": ""},
    )
    assert result.returncode != 0
    return result.stderr


class TeardownGuideTests(unittest.TestCase):
    def test_guide_switches_only_variables_declared_in_variables_tf(self):
        section = teardown_section()
        declared = set(
            re.findall(r'^variable "(\w+)"', (INFRA / "variables.tf").read_text(), re.MULTILINE)
        )
        assigned = {
            name
            for block in code_blocks(section, "hcl")
            for name in re.findall(r"^\s*(\w+)\s*=", block, re.MULTILINE)
        }
        self.assertTrue(
            {"table_deletion_protection", "spa_force_destroy"} <= assigned,
            "撤去の tfvars の例に table_deletion_protection と spa_force_destroy がありません",
        )
        self.assertLessEqual(assigned, declared, "variables.tf にない変数をガイドが指定しています")
        self.assertRegex(section, r"table_deletion_protection\s*=\s*false")
        self.assertRegex(section, r"spa_force_destroy\s*=\s*true")

    def test_guide_deploy_commands_use_only_options_in_the_usage(self):
        usage = deploy_usage()
        options = dict(re.findall(r"\[(--[a-z-]+)(?: ([^\]]+))?\]", usage))
        self.assertIn("--destroy", options)
        stacks = set(options["--stack"].split("|"))
        section = teardown_section()
        commands = [
            " ".join(block.replace("\\\n", " ").split())
            for block in re.findall(
                r"^.*scripts/deploy\.sh.*(?:\\\n.*)*",
                "\n".join(code_blocks(section, "bash")),
                re.MULTILINE,
            )
        ]
        self.assertTrue(commands, "撤去の節に deploy.sh の実行例がありません")
        loop_stacks = set(
            name for loop in re.findall(r"for STACK in ([\w ]+);", section) for name in loop.split()
        )
        destroyed = set()
        for command in commands:
            words = command.split()
            with self.subTest(command=command):
                for word in words:
                    if word.startswith("--"):
                        self.assertIn(word, options, f"deploy.sh の使い方にないオプション {word}")
                if "--destroy" not in words:
                    # 保護を外すための通常の配信（本体）
                    self.assertNotIn("--stack", words)
                    continue
                if "--stack" in words:
                    value = words[words.index("--stack") + 1].strip('"')
                    targets = loop_stacks if value == "$STACK" else {value}
                    self.assertTrue(targets, "--stack の値が決まりません")
                    self.assertLessEqual(targets, stacks)
                    destroyed |= targets
                else:
                    destroyed.add("main")
        self.assertEqual(
            destroyed, stacks | {"main"}, "追加スタックと本体の撤去の実行例がそろっていません"
        )

    def test_guide_destroys_addon_stacks_before_main(self):
        section = teardown_section()
        addon = section.find("--destroy --stack")
        main = re.search(r"deploy\.sh --destroy infrastructure/backend\.hcl", section)
        self.assertGreaterEqual(addon, 0)
        self.assertIsNotNone(main)
        self.assertLess(addon, main.start(), "撤去の順番は「追加スタック → 本体」です")

    def test_prevent_destroy_line_named_in_guide_exists_in_main_tf(self):
        # 補助テーブルの lifecycle の中にあることは test_teardown_guards.py が確かめる
        line = "lifecycle { prevent_destroy = true }"
        self.assertIn(f"`{line}`", teardown_section())
        self.assertEqual((INFRA / "main.tf").read_text().count(f"  {line}\n"), 1)

    def test_readme_links_to_the_teardown_section(self):
        anchor = github_anchor(TEARDOWN_HEADING)
        readme = (PORTAL / "README.md").read_text()
        self.assertIn(f"docs/agents/registry-portal-terraform-guide-ja.md#{anchor})", readme)

    def test_guide_links_to_existing_adrs_including_teardown_adr(self):
        guide = GUIDE.read_text()
        links = re.findall(r"\]\(\.\./adr/([^)#]+)", guide)
        for link in links:
            with self.subTest(link=link):
                self.assertTrue((REPO / "docs/adr" / link).is_file())
        self.assertIn("0014-dev-teardown-protection-and-scope.md", links)
        related = guide[guide.index("\n## 関連文書\n") :]
        self.assertIn("../adr/0014-dev-teardown-protection-and-scope.md", related)


if __name__ == "__main__":
    unittest.main()
