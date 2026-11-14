"""handler が補助テーブルに送る DynamoDB の操作を、IAM の action として記録する補助。

実行ロールのポリシー（IaC のテンプレート）が、実際に送られる操作をすべて許可しているかの検査に使う。
TransactWriteItems 自体には IAM の action がなく、項目ごとの種類（Put・Delete・Update・ConditionCheck）が
それぞれの action（PutItem・DeleteItem・UpdateItem・ConditionCheckItem）で認可される。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MODULES = Path(__file__).resolve().parents[2] / "infrastructure/modules"
# 実行ロールごとのポリシーのテンプレート（管理・決裁・イベント処理）
DRAFT_POLICY = MODULES / "management_drafts/draft-policy.json.tftpl"
DECISION_POLICY = MODULES / "decision/decision-policy.json.tftpl"
EVENTS_POLICY = MODULES / "notification_events/events-policy.json.tftpl"

# TransactWriteItems の項目の種類と、それを認可する IAM の action
TRANSACT_ITEM_ACTIONS = {
    "Put": "dynamodb:PutItem",
    "Delete": "dynamodb:DeleteItem",
    "Update": "dynamodb:UpdateItem",
    "ConditionCheck": "dynamodb:ConditionCheckItem",
}


class DynamoDbActions:
    """クライアントが送った DynamoDB の操作を、IAM の action の集合として記録する。"""

    def __init__(self, client: Any) -> None:
        self._actions: set[str] = set()
        client.meta.events.register("before-parameter-build.dynamodb", self._record)

    def _record(self, params: dict[str, Any], model: Any, **_: Any) -> None:
        if model.name == "TransactWriteItems":
            for item in params["TransactItems"]:
                (kind,) = item
                self._actions.add(TRANSACT_ITEM_ACTIONS[kind])
        else:
            self._actions.add(f"dynamodb:{model.name}")

    def take(self) -> set[str]:
        """記録した action を返し、記録を空にする（handler ごとに区切って検査する）。"""
        actions, self._actions = self._actions, set()
        return actions


def granted_dynamodb_actions(template: Path) -> set[str]:
    """ポリシーのテンプレートの Allow 文が許可する DynamoDB の action。

    action だけを読むため、templatefile の変数（ARN）は仮の値に置き換える。
    """
    rendered = re.sub(r"\$\{[a-z_]+\}", "arn:aws:placeholder", template.read_text())
    granted: set[str] = set()
    for statement in json.loads(rendered)["Statement"]:
        if statement["Effect"] != "Allow":
            continue
        actions = statement["Action"]
        for action in [actions] if isinstance(actions, str) else actions:
            if action.startswith("dynamodb:"):
                granted.add(action)
    return granted
