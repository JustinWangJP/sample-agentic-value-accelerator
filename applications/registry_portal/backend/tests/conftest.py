"""テスト共通の準備。

../scripts の運用スクリプト（検証スクリプトなど）を、テストから import できるようにする。
スクリプトの品質検査（flake8・black）は README の「開発」のとおり ../scripts も対象。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
