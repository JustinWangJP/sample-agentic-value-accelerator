from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import sys
from typing import Any, NamedTuple

# Registry の書き込み action。IAM では agent-registry と bedrock-agentcore の両方の prefix で禁止する
REGISTRY_SERVICE_PREFIXES = ("agent-registry", "bedrock-agentcore")
REGISTRY_WRITE_ACTIONS = (
    "CreateRegistry",
    "UpdateRegistry",
    "DeleteRegistry",
    "CreateRegistryRecord",
    "UpdateRegistryRecord",
    "UpdateRegistryRecordStatus",
    "SubmitRegistryRecordForApproval",
    "DeleteRegistryRecord",
)
FORBIDDEN_ACTIONS = tuple(
    f"{prefix}:{action}"
    for prefix in REGISTRY_SERVICE_PREFIXES
    for action in REGISTRY_WRITE_ACTIONS
) + (
    "lambda:CreateFunctionUrlConfig",
    "lambda:UpdateFunctionUrlConfig",
    "lambda:InvokeFunctionUrl",
)


class ScopedPolicy(NamedTuple):
    """追加スタックの module が作る inline policy。plan 上では module 呼び出し名と resource 名で特定する。"""

    module: str
    name: str

    @property
    def address(self) -> str:
        return f"module.{self.module}.aws_iam_role_policy.{self.name}"


# 例外として、対象 Registry に限って書き込みを許可する policy と action（小文字）
ROLE_SCOPED_ACTIONS = {
    ScopedPolicy("management_drafts", "management_drafts"): {
        "agent-registry:createregistryrecord",
        "agent-registry:updateregistryrecord",
        "agent-registry:submitregistryrecordforapproval",
    },
    ScopedPolicy("decision", "decision"): {
        "agent-registry:updateregistryrecordstatus",
    },
}
_SCOPED_ACTIONS_BY_ADDRESS = {
    policy.address: actions for policy, actions in ROLE_SCOPED_ACTIONS.items()
}
POLICY_TYPES = {
    "aws_iam_policy",
    "aws_iam_role_policy",
    "aws_iam_user_policy",
    "aws_iam_group_policy",
    "aws_ssoadmin_permission_set_inline_policy",
    "aws_iam_role",
}

MANAGED_POLICY_ATTACHMENTS = {
    "aws_iam_role_policy_attachment",
    "aws_iam_policy_attachment",
    "aws_iam_user_policy_attachment",
    "aws_iam_group_policy_attachment",
    "aws_iam_role_policy_attachments_exclusive",
    "aws_iam_user_policy_attachments_exclusive",
    "aws_iam_group_policy_attachments_exclusive",
    "aws_ssoadmin_managed_policy_attachment",
    "aws_ssoadmin_managed_policy_attachments_exclusive",
    "aws_ssoadmin_customer_managed_policy_attachment",
    "aws_ssoadmin_customer_managed_policy_attachments_exclusive",
}


def _resources(module: dict[str, Any]) -> list[dict[str, Any]]:
    result = list(module.get("resources", []))
    for child in module.get("child_modules", []):
        result.extend(_resources(child))
    return result


def _policies(values: Any) -> list[dict[str, Any]]:
    if isinstance(values, str):
        return [json.loads(values)]
    if isinstance(values, list):
        return [policy for item in values for policy in _policies(item)]
    if isinstance(values, dict):
        if "Statement" in values:
            return [values]
        return [
            policy
            for key, value in values.items()
            if key in {"policy", "inline_policy"}
            for policy in _policies(value)
        ]
    return []


def _target_registry_arn(plan: dict[str, Any]) -> str | None:
    """追加スタックのルート変数 registry_arn（借用する既存 Registry）を返す。"""
    variable = (plan.get("variables") or {}).get("registry_arn") or {}
    value = variable.get("value")
    if not isinstance(value, str) or not value or any(char in value for char in "*?"):
        return None
    return value


def _limited_to_registry(statement: dict[str, Any], registry_arn: str | None) -> bool:
    """Resource が対象 Registry 本体とそのレコードだけに限られているかを返す。"""
    if registry_arn is None or "NotResource" in statement:
        return False
    resources = statement.get("Resource", [])
    if isinstance(resources, str):
        resources = [resources]
    record_prefix = f"{registry_arn}/record/"
    return bool(resources) and all(
        isinstance(item, str)
        and (
            item == registry_arn
            or (item.startswith(record_prefix) and "/" not in item[len(record_prefix) :])
        )
        for item in resources
    )


def check_plan(plan: dict[str, Any]) -> list[str]:
    """保存済み plan から安全条件に違反する箇所を返す。"""
    if not str(plan.get("format_version", "")).startswith("1."):
        raise ValueError("対応する plan JSON の format_version がありません")
    root = plan["planned_values"]["root_module"]
    registry_arn = _target_registry_arn(plan)
    errors = []
    changes = plan.get("resource_changes", [])
    for resource in changes:
        if resource.get("mode") == "data":
            continue
        resource_type = resource["type"]
        address = resource["address"]
        identities = [resource["change"].get(side) or {} for side in ("before", "after")]
        has_registry_arn = any(
            str(values.get(key, "")).startswith("arn:aws:agent-registry:")
            for values in identities
            for key in ("arn", "id")
        )
        lowered_type = resource_type.lower()
        if has_registry_arn or (
            "registry" in lowered_type
            and ("agent_registry" in lowered_type or "agentcore" in lowered_type)
        ):
            errors.append(f"{address}: 既存 Registry を Terraform の管理対象にできません")
        if resource_type in POLICY_TYPES:
            unknown = resource["change"].get("after_unknown", {})
            if resource_type == "aws_iam_role" and unknown.get("managed_policy_arns"):
                errors.append(f"{address}: 未確定の managed policy の付与は検査できません")
            if any(unknown.get(key) for key in ("policy", "inline_policy")):
                errors.append(f"{address}: 未確定の IAM policy は検査できません")
    # 計画後の値（planned_values）と変更（削除を含む resource_changes）を 1 つの流れで検査する
    resources = _resources(root)
    resources += [
        dict(resource, values=resource["change"].get("after") or {})
        for resource in changes
        if resource.get("mode") != "data"
    ]
    for resource in resources:
        resource_type = resource["type"]
        address = resource["address"]
        if resource_type == "aws_lambda_function_url":
            errors.append(f"{address}: Function URL は使用できません")
        values = resource.get("values", {})
        if resource_type in MANAGED_POLICY_ATTACHMENTS or (
            resource_type == "aws_iam_role" and values.get("managed_policy_arns")
        ):
            errors.append(f"{address}: 内容を検査できない managed policy の付与は使用できません")
        if resource_type not in POLICY_TYPES:
            continue
        scoped = (
            _SCOPED_ACTIONS_BY_ADDRESS.get(address, set())
            if resource_type == "aws_iam_role_policy"
            else set()
        )
        for policy in _policies(values):
            statements = policy["Statement"]
            if isinstance(statements, dict):
                statements = [statements]
            for statement in statements:
                if statement.get("Effect") != "Allow":
                    continue
                if "NotAction" in statement:
                    errors.append(f"{address}: Allow の NotAction は使用できません")
                actions = statement.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                for action in actions:
                    if action.lower() in scoped:
                        if not _limited_to_registry(statement, registry_arn):
                            errors.append(
                                f"{address}: {action} の Resource は対象 Registry"
                                f"（{registry_arn or '不明'}）とそのレコードに限ってください"
                            )
                        continue
                    if any(
                        fnmatch.fnmatchcase(item.lower(), action.lower())
                        for item in FORBIDDEN_ACTIONS
                    ):
                        errors.append(f"{address}: 禁止 action {action}")
                    if (
                        resource_type != "aws_ssoadmin_permission_set_inline_policy"
                        and fnmatch.fnmatchcase("iam:passrole", action.lower())
                    ):
                        errors.append(f"{address}: 実行ロールに iam:PassRole を付与できません")
    return sorted(set(errors))


def main() -> int:
    try:
        plan = json.loads(Path(sys.argv[1]).read_text())
        errors = check_plan(plan)
    except (IndexError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"plan を検査できません: {error}", file=sys.stderr)
        return 1
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("plan ガードを通過しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
