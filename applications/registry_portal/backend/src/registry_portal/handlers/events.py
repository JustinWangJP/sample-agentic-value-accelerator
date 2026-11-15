"""イベント処理 handler の Lambda エントリーポイント。

EventBridge のルールから、Registry のレコードの状態変化のイベントを受け取る。
Lambda の初期化時に設定を読み込み、Registry の ARN または補助テーブルが不足していれば起動しない。
"""

from __future__ import annotations

import os

from registry_portal.app import create_event_handler

handler = create_event_handler(os.environ)
