from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import sys
from typing import Any

FORBIDDEN_ACTIONS = (
    "agent-registry:CreateRegistry",
    "agent-registry:UpdateRegistry",
    "agent-registry:DeleteRegistry",
    "agent-registry:DeleteRegistryRecord",
    "agent-registry:CreateRegistryRecord",
    "agent-registry:UpdateRegistryRecord",
    "agent-registry:UpdateRegistryRecordStatus",
    "bedrock-agentcore:CreateRegistry",
    "bedrock-agentcore:UpdateRegistry",
    "bedrock-agentcore:DeleteRegistry",
    "bedrock-agentcore:CreateRegistryRecord",
    "bedrock-agentcore:UpdateRegistryRecord",
    "bedrock-agentcore:UpdateRegistryRecordStatus",
    "bedrock-agentcore:DeleteRegistryRecord",
    "lambda:CreateFunctionUrlConfig",
    "lambda:UpdateFunctionUrlConfig",
    "lambda:InvokeFunctionUrl",
)
POLICY_TYPES = {
    "aws_iam_policy",
    "aws_iam_role_policy",
    "aws_iam_user_policy",
    "aws_iam_group_policy",
    "aws_ssoadmin_permission_set_inline_policy",
    "aws_iam_role",
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


def check_plan(plan: dict[str, Any]) -> list[str]:
    """保存済み plan から安全条件に違反する箇所を返す。"""
    if not str(plan.get("format_version", "")).startswith("1."):
        raise ValueError("対応する plan JSON の format_version がありません")
    root = plan["planned_values"]["root_module"]
    errors = []
    changes = plan.get("resource_changes", [])
    for resource in changes:
        if resource.get("mode") == "data":
            continue
        kind = resource["type"].lower()
        address = resource["address"]
        identities = [resource["change"].get(side) or {} for side in ("before", "after")]
        has_registry_arn = any(
            str(values.get(key, "")).startswith("arn:aws:agent-registry:")
            for values in identities
            for key in ("arn", "id")
        )
        if has_registry_arn or (
            "registry" in kind and ("agent_registry" in kind or "agentcore" in kind)
        ):
            errors.append(f"{address}: 既存 Registry を Terraform の管理対象にできません")
        if kind == "aws_lambda_function_url":
            errors.append(f"{address}: Function URL は使用できません")
        if resource["type"] in POLICY_TYPES:
            unknown = resource["change"].get("after_unknown", {})
            if any(unknown.get(key) for key in ("policy", "inline_policy")):
                errors.append(f"{address}: 未確定の IAM policy は検査できません")
    resources = _resources(root)
    resources += [
        dict(resource, values=resource["change"].get("after") or {})
        for resource in changes
        if resource.get("mode") != "data"
    ]
    for resource in resources:
        kind = resource["type"]
        address = resource["address"]
        if kind == "aws_lambda_function_url":
            errors.append(f"{address}: Function URL は使用できません")
        values = resource.get("values", {})
        if kind in {
            "aws_iam_role_policy_attachment",
            "aws_iam_policy_attachment",
            "aws_iam_user_policy_attachment",
            "aws_iam_group_policy_attachment",
        } or (kind == "aws_iam_role" and values.get("managed_policy_arns")):
            errors.append(f"{address}: 内容を検査できない managed policy の付与は使用できません")
        if kind not in POLICY_TYPES:
            continue
        for policy in _policies(resource.get("values", {})):
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
                    if any(
                        fnmatch.fnmatchcase(item.lower(), action.lower())
                        for item in FORBIDDEN_ACTIONS
                    ):
                        errors.append(f"{address}: 禁止 action {action}")
                    if kind != "aws_ssoadmin_permission_set_inline_policy" and fnmatch.fnmatchcase(
                        "iam:passrole", action.lower()
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
