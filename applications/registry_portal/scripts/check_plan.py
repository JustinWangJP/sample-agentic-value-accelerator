from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, NamedTuple
import zipfile

# Registry の書き込み action。IAM では agent-registry と bedrock-agentcore の両方の prefix で禁止する。
# bootstrap の permission set（Deny）と同じファイルを読み、一覧を 1 か所に保つ
_REGISTRY_WRITE = json.loads(
    Path(__file__).with_name("registry_write_actions.json").read_text(encoding="utf-8")
)
REGISTRY_SERVICE_PREFIXES: tuple[str, ...] = tuple(_REGISTRY_WRITE["service_prefixes"])
REGISTRY_WRITE_ACTIONS: tuple[str, ...] = tuple(_REGISTRY_WRITE["actions"])
FORBIDDEN_ACTIONS = tuple(
    f"{prefix}:{action}"
    for prefix in REGISTRY_SERVICE_PREFIXES
    for action in REGISTRY_WRITE_ACTIONS
) + (
    "lambda:CreateFunctionUrlConfig",
    "lambda:UpdateFunctionUrlConfig",
    "lambda:InvokeFunctionUrl",
)
# IAM の action 名は大文字と小文字を区別しないため、照合は小文字どうしで行う
_FORBIDDEN_LOWER = tuple(action.lower() for action in FORBIDDEN_ACTIONS)


class ScopedPolicy(NamedTuple):
    """追加スタックの module が作る inline policy。plan 上では module 呼び出し名と resource 名で特定する。"""

    module: str
    name: str

    @property
    def address(self) -> str:
        return f"module.{self.module}.aws_iam_role_policy.{self.name}"


# 例外として、対象 Registry に限って書き込みを許可する policy と action（小文字）。
# policy は plan 上のアドレス（module 呼び出し名と resource 名）で特定する。ガードの側で決める例外のため、
# Terraform 側の目印（Sid など）には頼らない。Terraform 側で改名すると例外が外れて中止になる（安全側に倒れる）。
# 改名に気づけるよう、tests/test_plan_guard.py がこのアドレスが追加スタックの定義に実在することを確かめる
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
_MODULE_NAME = r"[^.\[\]/]+"


def _as_list(value: Any) -> list[Any]:
    """IAM policy の要素（Statement・Action・Resource）は単一の値でもよいため、list にそろえる。"""
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        return [value]
    return list(value)


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


def _module_names(change: dict[str, Any]) -> tuple[str, ...] | None:
    address = change.get("module_address") or ""
    # Terraform が生成する HCL の引用文字列は、エスケープや ] も含めて添字として読む。
    step = rf'module\.({_MODULE_NAME})(?:\[(?:[0-9]+|"(?:[^"\\]|\\.)*")\])?'
    if not isinstance(address, str) or (
        address and re.fullmatch(rf"{step}(?:\.{step})*", address) is None
    ):
        return None
    return tuple(re.findall(step, address))


def _configured_resource(plan: dict[str, Any], change: dict[str, Any]) -> dict[str, Any] | None:
    """resource_changes の要素に対応する configuration の resource を返す。照合できなければ None。"""
    module = (plan.get("configuration") or {}).get("root_module")
    names = _module_names(change)
    if module is None or names is None:
        return None
    for name in names:
        module = ((module.get("module_calls") or {}).get(name) or {}).get("module")
        if module is None:
            return None
    address = f"{change.get('type')}.{change.get('name')}"
    return next(
        (
            resource
            for resource in module.get("resources", [])
            if resource.get("mode") == "managed" and resource.get("address") == address
        ),
        None,
    )


def _verified_modules(saved_plan: Path) -> frozenset[tuple[str, ...]]:
    """同じ保存 plan の設定から、dynamic がない native HCL の module だけを確認する。"""
    try:
        with zipfile.ZipFile(saved_plan) as archive:
            entries = archive.namelist()
            if len(entries) != len(set(entries)):
                return frozenset()
            manifest = json.loads(archive.read("tfconfig/modules.json"))
            if not isinstance(manifest, list):
                return frozenset()
            keys = [item.get("Key") for item in manifest if isinstance(item, dict)]
            if (
                len(keys) != len(manifest)
                or any(not isinstance(key, str) for key in keys)
                or len(keys) != len(set(keys))
                or "" not in keys
            ):
                return frozenset()
            verified = set()
            for key in keys:
                if key and re.fullmatch(rf"{_MODULE_NAME}(?:\.{_MODULE_NAME})*", key) is None:
                    return frozenset()
                prefix = f"tfconfig/m-{key}/"
                sources = [
                    name for name in entries if name.startswith(prefix) and not name.endswith("/")
                ]
                if (
                    any(name.endswith(".tf") for name in sources)
                    and not any(name.endswith(".tf.json") for name in sources)
                    and all(b"dynamic" not in archive.read(name) for name in sources)
                ):
                    verified.add(tuple(key.split(".")) if key else ())
            return frozenset(verified)
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile):
        return frozenset()


def _computed_on_create(
    plan: dict[str, Any],
    change: dict[str, Any],
    key: str,
    verified_modules: frozenset[tuple[str, ...]],
) -> bool:
    """ロールの作成で、設定にない属性が provider の computed のために未確定になっているかを返す。

    inline_policy の式は dynamic ブロックでも省略されるため、保存 plan のソース確認も必要とする。
    """
    if (
        key not in {"managed_policy_arns", "inline_policy"}
        or change.get("type") != "aws_iam_role"
        or "create" not in change["change"].get("actions", [])
    ):
        return False
    resource = _configured_resource(plan, change)
    if resource is None:
        return False
    expressions = resource.get("expressions")
    if not isinstance(expressions, dict):
        return False
    if key == "managed_policy_arns":
        return key not in expressions or expressions[key] == {"constant_value": []}
    return (
        key not in expressions
        and change["change"].get("after_unknown", {}).get(key) is True
        and _module_names(change) in verified_modules
    )


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
    resources = _as_list(statement.get("Resource"))
    record_prefix = f"{registry_arn}/record/"
    return bool(resources) and all(
        isinstance(item, str)
        and (
            item == registry_arn
            or (item.startswith(record_prefix) and "/" not in item[len(record_prefix) :])
        )
        for item in resources
    )


def check_plan(
    plan: dict[str, Any], verified_modules: frozenset[tuple[str, ...]] = frozenset()
) -> list[str]:
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
            unknown = {
                key: value
                for key, value in (resource["change"].get("after_unknown") or {}).items()
                if value and not _computed_on_create(plan, resource, key, verified_modules)
            }
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
            for statement in _as_list(policy["Statement"]):
                if statement.get("Effect") != "Allow":
                    continue
                if "NotAction" in statement:
                    errors.append(f"{address}: Allow の NotAction は使用できません")
                for action in _as_list(statement.get("Action")):
                    lowered = action.lower()
                    if lowered in scoped:
                        if not _limited_to_registry(statement, registry_arn):
                            errors.append(
                                f"{address}: {action} の Resource は対象 Registry"
                                f"（{registry_arn or '不明'}）とそのレコードに限ってください"
                            )
                        continue
                    if any(fnmatch.fnmatchcase(item, lowered) for item in _FORBIDDEN_LOWER):
                        errors.append(f"{address}: 禁止 action {action}")
                    if (
                        resource_type != "aws_ssoadmin_permission_set_inline_policy"
                        and fnmatch.fnmatchcase("iam:passrole", lowered)
                    ):
                        errors.append(f"{address}: 実行ロールに iam:PassRole を付与できません")
    return sorted(set(errors))


def main() -> int:
    try:
        args = sys.argv[1:]
        verified_modules = frozenset()
        if len(args) == 2 and args[0] == "--saved-plan":
            saved_plan = Path(args[1]).resolve()
            try:
                result = subprocess.run(
                    ["terraform", "show", "-json", str(saved_plan)],
                    capture_output=True,
                    check=True,
                )
            except (OSError, subprocess.CalledProcessError):
                raise ValueError("保存 plan を Terraform で読み取れません") from None
            plan = json.loads(result.stdout)
            verified_modules = _verified_modules(saved_plan)
        elif len(args) == 1 and not args[0].startswith("-"):
            plan = json.loads(Path(args[0]).read_text())
        else:
            raise ValueError(
                "使い方: check_plan.py <plan.json> または check_plan.py --saved-plan <tfplan>"
            )
        errors = check_plan(plan, verified_modules)
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
