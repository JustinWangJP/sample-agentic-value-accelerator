"""決裁 handler の Lambda エントリーポイント。

Lambda の初期化時に設定を読み込む。認証設定または補助テーブルが不足していれば
import に失敗し、起動しない。
"""

from __future__ import annotations

import os

from registry_portal.app import create_curation_handler

handler = create_curation_handler(os.environ)
