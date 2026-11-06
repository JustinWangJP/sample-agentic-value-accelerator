"""種別（recordType）と primary descriptor の対応、descriptor ごとの入力項目・検証・AWS の payload。

AWS が受理する組合せは公式の資料（registry-supported-record-types）に従う。ポータルは primary descriptor を
1個だけ組み立てる。フォームの入力は文字列だけの平らな辞書で、descriptor の種類ごとに項目が異なる。
SPA の `frontend/src/drafts/descriptors.ts` と値を揃える。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Literal, get_args

from registry_portal.domain.catalog import (
    A2A_DESCRIPTOR_TYPE,
    AGENT_RECORD_TYPE,
    CATALOG_RECORD_TYPES,
    CatalogRecordType,
)
from registry_portal.domain.secrets import SecretFinding, scan_json, scan_text

DescriptorType = Literal["custom", "a2aAgentCard", "mcpServer", "agentSkillsDefinition"]
DESCRIPTOR_TYPES: tuple[DescriptorType, ...] = get_args(DescriptorType)
CUSTOM_DESCRIPTOR_TYPE: DescriptorType = "custom"
MCP_DESCRIPTOR_TYPE: DescriptorType = "mcpServer"
SKILL_DESCRIPTOR_TYPE: DescriptorType = "agentSkillsDefinition"

# 種別ごとに受け付ける primary descriptor。先頭を既定とする。
# Agent → custom / a2aAgentCard、MCP → mcpServer、Skill → agentSkillsDefinition
RECORD_TYPE_DESCRIPTORS: Mapping[CatalogRecordType, tuple[DescriptorType, ...]] = {
    "AGENT": (CUSTOM_DESCRIPTOR_TYPE, A2A_DESCRIPTOR_TYPE),
    "MCP": (MCP_DESCRIPTOR_TYPE,),
    "SKILL": (SKILL_DESCRIPTOR_TYPE,),
}

# descriptor の dataSchemaVersion として受け付ける値（AWS の公式資料に記載の版）
A2A_SCHEMA_VERSIONS = ("0.3",)
MCP_SERVER_SCHEMA_VERSIONS = (
    "2025-12-11",
    "2025-10-17",
    "2025-10-11",
    "2025-09-29",
    "2025-09-16",
    "2025-07-09",
)
MCP_TOOLS_SCHEMA_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SKILL_SCHEMA_VERSIONS = ("0.1.0",)
SCHEMA_VERSIONS: Mapping[DescriptorType, tuple[str, ...]] = {
    "a2aAgentCard": A2A_SCHEMA_VERSIONS,
    "mcpServer": MCP_SERVER_SCHEMA_VERSIONS,
    "agentSkillsDefinition": SKILL_SCHEMA_VERSIONS,
}

# descriptor の種類ごとの入力項目（共通項目を除く）
CUSTOM_FIELDS = ("purpose", "authentication")
A2A_FIELDS = ("schemaVersion", "definition")
MCP_FIELDS = ("schemaVersion", "definition", "toolsSchemaVersion", "toolsDefinition")
SKILL_FIELDS = (
    "schemaVersion",
    "skillRepositoryUrl",
    "skillRepositorySource",
    "skillCommit",
    "skillVersion",
    "skillDigest",
    "skillMd",
)
DESCRIPTOR_FIELDS: Mapping[DescriptorType, tuple[str, ...]] = {
    "custom": CUSTOM_FIELDS,
    "a2aAgentCard": A2A_FIELDS,
    "mcpServer": MCP_FIELDS,
    "agentSkillsDefinition": SKILL_FIELDS,
}
# 取得できなければ空でよい項目（設計書「秘密情報と外部配布物」: digest は取得できれば記録する）
OPTIONAL_FIELDS = frozenset({"skillDigest"})
# 補助ストアの管理情報にだけ置く、Skill の配布物の固定情報。AWS の skill definition（0.1.0）には
# commit・digest の専用項目がないため、補助ストアを正本とする（ADR-0009）
SKILL_SUPPLEMENT_FIELDS = ("skillCommit", "skillVersion", "skillDigest")

# 構造を解釈して秘密情報を検査する項目（normalize_and_check が検査する）
STRUCTURED_FIELDS = frozenset({"definition", "toolsDefinition", "skillMd"})

# 定義の文字数上限。補助ストアの 1 項目（400 KB）に、操作の入力と結果の2か所で定義を保存するため、
# AWS の上限（102,400 文字）より小さくする
DEFINITION_LIMITS = {"definition": 24000, "toolsDefinition": 24000, "skillMd": 32000}

_A2A_CARD_REQUIRED = (
    "name",
    "description",
    "version",
    "protocolVersion",
    "url",
    "capabilities",
    "defaultInputModes",
    "defaultOutputModes",
    "skills",
)
_MCP_SERVER_REQUIRED = ("name", "description", "version")
_SKILL_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[^\s?#@]*)?")
_SKILL_SOURCE = re.compile(r"[a-z][a-z0-9-]{0,63}")
_SKILL_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SKILL_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SKILL_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# 「固定」ではない版（可変の参照）
_FLOATING_VERSIONS = frozenset({"latest", "main", "master", "head", "develop", "stable", "current"})
_WILDCARD_VERSION = re.compile(r"(?:^|[.-])[xX*](?:$|[.-])")
_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


def default_descriptor_type(record_type: CatalogRecordType) -> DescriptorType:
    return RECORD_TYPE_DESCRIPTORS[record_type][0]


def derive_is_a2a(record_type: str, descriptor_type: str) -> bool:
    """Agent の isA2A。descriptor から導出し、利用者の入力にはしない（ADR-0006）。"""
    return record_type == AGENT_RECORD_TYPE and descriptor_type == A2A_DESCRIPTOR_TYPE


def custom_metadata_extra(record_type: str, descriptor_type: str) -> dict[str, bool]:
    """custom metadata に足す項目。isA2A は Agent にだけ付ける（MCP・Skill は schema にない）。"""
    if record_type != AGENT_RECORD_TYPE:
        return {}
    return {"isA2A": derive_is_a2a(record_type, descriptor_type)}


def check_kind(record_type: str, descriptor_type: str) -> tuple[CatalogRecordType, DescriptorType]:
    """種別と descriptor の組合せを検査する。AWS が受理しない組合せは日本語で拒否する。"""
    if record_type not in CATALOG_RECORD_TYPES:
        raise ValueError(
            f"recordType は {' / '.join(CATALOG_RECORD_TYPES)} のいずれかを指定してください。"
        )
    kind = next(kind for kind in CATALOG_RECORD_TYPES if kind == record_type)
    allowed = RECORD_TYPE_DESCRIPTORS[kind]
    if descriptor_type not in DESCRIPTOR_TYPES or descriptor_type not in allowed:
        raise ValueError(
            f"種別 {kind} に指定できる descriptor は {' / '.join(allowed)} です"
            f"（指定: {descriptor_type}）。"
        )
    return kind, next(item for item in allowed if item == descriptor_type)


def _json_object(label: str, text: str) -> dict[str, Any]:
    def reject_constant(name: str) -> Any:
        raise ValueError(name)

    try:
        value = json.loads(text, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise ValueError(f"{label} は JSON として読み取れません。") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} は JSON オブジェクトで指定してください。")
    return value


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _require_keys(label: str, value: Mapping[str, Any], keys: tuple[str, ...]) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        raise ValueError(f"{label} に必須項目がありません: {', '.join(missing)}。")


def _check_version(label: str, version: str, allowed: tuple[str, ...]) -> None:
    if version not in allowed:
        raise ValueError(
            f"{label} の schema version は {' / '.join(allowed)} のいずれかを指定してください"
            f"（指定: {version}）。"
        )


def _check_tools(value: Mapping[str, Any]) -> None:
    tools = value.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError("tools 定義には tools に1個以上のツールを指定してください。")
    for index, tool in enumerate(tools):
        if (
            not isinstance(tool, dict)
            or not isinstance(tool.get("name"), str)
            or not tool["name"]
            or not isinstance(tool.get("inputSchema"), dict)
        ):
            raise ValueError(f"tools[{index}] には name と inputSchema（オブジェクト）が必要です。")


def _check_skill_md(text: str) -> None:
    match = _FRONT_MATTER.match(text)
    names = {
        line.split(":", 1)[0].strip()
        for line in (match.group(1).splitlines() if match else [])
        if ":" in line and line.split(":", 1)[1].strip()
    }
    if not {"name", "description"} <= names:
        raise ValueError("SKILL.md の先頭に name と description を持つ frontmatter が必要です。")


def _check_skill_pin(fields: Mapping[str, str]) -> None:
    url = fields["skillRepositoryUrl"]
    if not _SKILL_URL.fullmatch(url):
        raise ValueError(
            "リポジトリの URL は https:// で始まり、資格情報やクエリを含まない形式で指定してください。"
        )
    if not _SKILL_SOURCE.fullmatch(fields["skillRepositorySource"]):
        raise ValueError("リポジトリの種類は小文字英数字と - で指定してください（例 github）。")
    if not _SKILL_COMMIT.fullmatch(fields["skillCommit"]):
        raise ValueError(
            "固定した commit は 40 文字（SHA-1）または 64 文字（SHA-256）の小文字 16 進数で指定してください。"
        )
    version = fields["skillVersion"]
    if (
        not _SKILL_VERSION.fullmatch(version)
        or version.lower() in _FLOATING_VERSIONS
        or _WILDCARD_VERSION.search(version)
    ):
        raise ValueError(
            "固定した version には、latest・ブランチ名・範囲指定ではなく、特定の版を指定してください。"
        )
    digest = fields.get("skillDigest", "")
    if digest and not _SKILL_DIGEST.fullmatch(digest):
        raise ValueError("digest は sha256: に続く 64 文字の小文字 16 進数で指定してください。")


def normalize_and_check(
    descriptor_type: DescriptorType, fields: dict[str, str]
) -> list[SecretFinding]:
    """descriptor ごとの定義を検査し、JSON は compact にそろえる。秘密情報らしき値の検出結果を返す。

    不正な schema version・形式は ValueError（日本語）。fields は書き換える。
    """
    findings: list[SecretFinding] = []
    if descriptor_type == A2A_DESCRIPTOR_TYPE:
        _check_version("A2A Agent Card", fields["schemaVersion"], A2A_SCHEMA_VERSIONS)
        card = _json_object("Agent Card", fields["definition"])
        _require_keys("Agent Card", card, _A2A_CARD_REQUIRED)
        fields["definition"] = compact_json(card)
        findings += scan_json("definition", card)
    elif descriptor_type == MCP_DESCRIPTOR_TYPE:
        _check_version("MCP サーバー定義", fields["schemaVersion"], MCP_SERVER_SCHEMA_VERSIONS)
        _check_version("MCP の tools 定義", fields["toolsSchemaVersion"], MCP_TOOLS_SCHEMA_VERSIONS)
        server = _json_object("MCP サーバー定義", fields["definition"])
        _require_keys("MCP サーバー定義", server, _MCP_SERVER_REQUIRED)
        tools = _json_object("MCP の tools 定義", fields["toolsDefinition"])
        _check_tools(tools)
        fields["definition"] = compact_json(server)
        fields["toolsDefinition"] = compact_json(tools)
        findings += scan_json("definition", server) + scan_json("toolsDefinition", tools)
    elif descriptor_type == SKILL_DESCRIPTOR_TYPE:
        _check_version("Skill の定義", fields["schemaVersion"], SKILL_SCHEMA_VERSIONS)
        _check_skill_pin(fields)
        _check_skill_md(fields["skillMd"])
        findings += scan_text("skillMd", fields["skillMd"])
    return findings


def _skill_definition(fields: Mapping[str, str]) -> str:
    return compact_json(
        {
            "repository": {
                "url": fields["skillRepositoryUrl"],
                "source": fields["skillRepositorySource"],
            }
        }
    )


def create_descriptors(
    descriptor_type: DescriptorType, fields: Mapping[str, str]
) -> dict[str, Any]:
    """CreateRegistryRecord の descriptors（custom は呼び出し側が組み立てる定義の JSON を渡す）。"""
    if descriptor_type == A2A_DESCRIPTOR_TYPE:
        return {
            "a2aAgentCard": {
                "data": fields["definition"],
                "dataSchemaVersion": fields["schemaVersion"],
            }
        }
    if descriptor_type == MCP_DESCRIPTOR_TYPE:
        return {
            "mcpServer": {
                "data": fields["definition"],
                "dataSchemaVersion": fields["schemaVersion"],
                "additionalData": {
                    "tools": {
                        "data": fields["toolsDefinition"],
                        "dataSchemaVersion": fields["toolsSchemaVersion"],
                    }
                },
            }
        }
    return {
        "agentSkillsDefinition": {
            "data": _skill_definition(fields),
            "dataSchemaVersion": fields["schemaVersion"],
            "additionalData": {"skillMd": {"data": fields["skillMd"]}},
        }
    }


def _patch(value: str) -> dict[str, str]:
    return {"optionalValue": value}


def update_descriptors(
    descriptor_type: DescriptorType, fields: Mapping[str, str], custom_data: str
) -> dict[str, Any]:
    """UpdateRegistryRecord の descriptors に入れる、descriptor 種類ごとの patch。"""
    if descriptor_type == CUSTOM_DESCRIPTOR_TYPE:
        return {"custom": {"optionalValue": {"data": _patch(custom_data)}}}
    created = create_descriptors(descriptor_type, fields)[descriptor_type]
    patch: dict[str, Any] = {
        "data": _patch(created["data"]),
        "dataSchemaVersion": _patch(created["dataSchemaVersion"]),
    }
    additional = created.get("additionalData")
    if descriptor_type == MCP_DESCRIPTOR_TYPE:
        tools = additional["tools"]
        patch["additionalData"] = {
            "optionalValue": {
                "tools": {
                    "optionalValue": {
                        "data": _patch(tools["data"]),
                        "dataSchemaVersion": _patch(tools["dataSchemaVersion"]),
                    }
                }
            }
        }
    elif descriptor_type == SKILL_DESCRIPTOR_TYPE:
        patch["additionalData"] = {
            "optionalValue": {
                "skillMd": {"optionalValue": {"data": _patch(additional["skillMd"]["data"])}}
            }
        }
    return {descriptor_type: {"optionalValue": patch}}


def read_descriptor_fields(
    descriptor_type: DescriptorType, descriptors: Mapping[str, Any], supplement: Mapping[str, Any]
) -> dict[str, str]:
    """AWS が返した descriptor から、descriptor 種類ごとの入力項目を読む。読めなければ KeyError など。"""
    descriptor = descriptors[descriptor_type]
    if descriptor_type == A2A_DESCRIPTOR_TYPE:
        return {"schemaVersion": descriptor["dataSchemaVersion"], "definition": descriptor["data"]}
    if descriptor_type == MCP_DESCRIPTOR_TYPE:
        tools = descriptor["additionalData"]["tools"]
        return {
            "schemaVersion": descriptor["dataSchemaVersion"],
            "definition": descriptor["data"],
            "toolsSchemaVersion": tools["dataSchemaVersion"],
            "toolsDefinition": tools["data"],
        }
    repository = _json_object("Skill の定義", descriptor["data"])["repository"]
    return {
        "schemaVersion": descriptor["dataSchemaVersion"],
        "skillRepositoryUrl": repository["url"],
        "skillRepositorySource": repository["source"],
        "skillCommit": supplement["skillCommit"],
        "skillVersion": supplement["skillVersion"],
        "skillDigest": supplement.get("skillDigest", ""),
        "skillMd": descriptor["additionalData"]["skillMd"]["data"],
    }


def _normalized(key: str, value: object) -> object:
    """descriptor の data（JSON の文字列）は、空白や並びの違いを無視して比べる。"""
    if key == "data" and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _subset_matches(sent: object, returned: object, key: str = "") -> bool:
    """送った項目がすべて、返された内容に含まれているか。AWS が補う項目（source など）は無視する。"""
    if isinstance(sent, Mapping):
        return isinstance(returned, Mapping) and all(
            name in returned and _subset_matches(value, returned[name], name)
            for name, value in sent.items()
        )
    return _normalized(key, sent) == _normalized(key, returned)


def descriptors_applied(sent: Mapping[str, Any], returned: Mapping[str, Any]) -> bool:
    """送った descriptors が AWS に適用されているか（primary descriptor が1個だけであることも含む）。"""
    return set(sent) == set(returned) and _subset_matches(sent, returned)
