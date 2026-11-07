"""種別（recordType）と primary descriptor の対応、descriptor ごとの入力項目・検証・AWS の payload。

AWS が受理する組合せは公式の資料（registry-supported-record-types）に従う。ポータルは primary descriptor を
1個だけ組み立てる。フォームの入力は文字列だけの平らな辞書で、descriptor の種類ごとに項目が異なる。
descriptor の種類ごとの違いは `_HANDLERS` の1か所にまとめ、他のモジュールは種類で分岐しない。
SPA の `frontend/src/drafts/descriptors.ts` と値を揃える。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from registry_portal.domain.catalog import (
    A2A_DESCRIPTOR_TYPE,
    AGENT_RECORD_TYPE,
    CATALOG_RECORD_TYPES,
    CUSTOM_DESCRIPTOR_TYPE,
    DESCRIPTOR_TYPES,
    MCP_DESCRIPTOR_TYPE,
    MCP_RECORD_TYPE,
    SKILL_DESCRIPTOR_TYPE,
    SKILL_RECORD_TYPE,
    CatalogRecordType,
    DescriptorType,
)
from registry_portal.domain.secrets import SecretFinding, scan_json, scan_text

# 種別ごとに受け付ける primary descriptor。先頭を既定とする。
# Agent → custom / a2aAgentCard、MCP → mcpServer、Skill → agentSkillsDefinition
RECORD_TYPE_DESCRIPTORS: Mapping[CatalogRecordType, tuple[DescriptorType, ...]] = {
    AGENT_RECORD_TYPE: (CUSTOM_DESCRIPTOR_TYPE, A2A_DESCRIPTOR_TYPE),
    MCP_RECORD_TYPE: (MCP_DESCRIPTOR_TYPE,),
    SKILL_RECORD_TYPE: (SKILL_DESCRIPTOR_TYPE,),
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

# すべての種別で入力する、連絡先・利用条件・接続先の参照。補助ストアの管理情報（MANAGEMENT）に保存する。
# custom descriptor では descriptor の中身を正本とし、管理情報は写しとして公開版スナップショットを作るために
# 使い、下書きの読み取りには使わない（ADR-0008）。AWS が検証する他の descriptor は、これらを入れる項目が
# ないため、管理情報を正本とする（ADR-0009）
SUPPLEMENT_FIELDS = ("contact", "termsOfUse", "connectionReference")

# descriptor の種類ごとの入力項目（共通項目を除く）
CUSTOM_FIELDS = ("purpose", "authentication")
# custom descriptor の data に canonical JSON として保存する定義
CUSTOM_DEFINITION_FIELDS = (*CUSTOM_FIELDS, *SUPPLEMENT_FIELDS)
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
    CUSTOM_DESCRIPTOR_TYPE: CUSTOM_FIELDS,
    A2A_DESCRIPTOR_TYPE: A2A_FIELDS,
    MCP_DESCRIPTOR_TYPE: MCP_FIELDS,
    SKILL_DESCRIPTOR_TYPE: SKILL_FIELDS,
}
# 空でよい項目。PRD のストーリー 19「固定した commit / version」のとおり commit と version は少なくとも
# 一方があればよく（_check_skill_pin が検査する）、digest は取得できれば記録する（設計書「秘密情報と外部配布物」）
OPTIONAL_FIELDS = frozenset({"skillCommit", "skillVersion", "skillDigest"})
# 補助ストアの管理情報にだけ置く、Skill の配布物の固定情報。AWS の skill definition（0.1.0）には
# commit・digest の専用項目がないため、補助ストアを正本とする（ADR-0009）
SKILL_SUPPLEMENT_FIELDS = ("skillCommit", "skillVersion", "skillDigest")
# 管理情報に保存し得るすべての項目（Skill の配布物の固定情報を含む）
ALL_SUPPLEMENT_FIELDS = (*SUPPLEMENT_FIELDS, *SKILL_SUPPLEMENT_FIELDS)

# 構造を解釈して秘密情報を検査する項目（normalize_and_check が検査する）
STRUCTURED_FIELDS = frozenset({"definition", "toolsDefinition", "skillMd"})

# 定義の文字数上限。補助ストアの 1 項目（400 KB）に、操作の入力と結果の2か所で定義を保存するため、
# AWS の上限（102,400 文字）より小さくする
DEFINITION_LIMITS = {"definition": 24000, "toolsDefinition": 24000, "skillMd": 32000}

# 必須項目の根拠は README「検証」節のリンク先（A2A 0.3 の AgentCard、MCP の server.json と Tool の JSON Schema）
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
# 特定の版は数字（または v と数字）で始める。latest・main・release-2025 などのブランチ名やタグ名は通さない
_SKILL_VERSION = re.compile(r"v?[0-9][A-Za-z0-9._-]{0,126}")
_SKILL_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# 範囲指定（1.x など）
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


def canonical(value: object) -> str:
    """キーを並べた compact JSON。custom descriptor の data と内容ハッシュに使う。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


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
    # MCP の Tool は name と inputSchema が必須で、inputSchema のルートは type: "object" が必須
    # （許可する tools の schema version のすべてで同じ。README「検証」節のリンク先）
    for index, tool in enumerate(tools):
        if (
            not isinstance(tool, dict)
            or not isinstance(tool.get("name"), str)
            or not tool["name"]
            or not isinstance(tool.get("inputSchema"), dict)
            or tool["inputSchema"].get("type") != "object"
        ):
            raise ValueError(
                f'tools[{index}] には name と、type が "object" の inputSchema が必要です。'
            )


def _frontmatter_keys(text: str) -> set[str]:
    """frontmatter の最上位で値を持つ項目名。値は同じ行か、YAML のブロック形式で次のインデントした行に書く。"""
    match = _FRONT_MATTER.match(text)
    lines = match.group(1).splitlines() if match else []
    keys: set[str] = set()
    for index, line in enumerate(lines):
        key, separator, value = line.partition(":")
        if not separator or line[0] in " \t" or not key.strip():
            continue
        following = next((later for later in lines[index + 1 :] if later.strip()), "")
        if value.strip() or following[:1] in (" ", "\t"):
            keys.add(key.strip())
    return keys


def _check_skill_md(text: str) -> None:
    if not {"name", "description"} <= _frontmatter_keys(text):
        raise ValueError("SKILL.md の先頭に name と description を持つ frontmatter が必要です。")


def _check_skill_pin(fields: Mapping[str, str]) -> None:
    url = fields["skillRepositoryUrl"]
    if not _SKILL_URL.fullmatch(url):
        raise ValueError(
            "リポジトリの URL は https:// で始まり、資格情報やクエリを含まない形式で指定してください。"
        )
    if not _SKILL_SOURCE.fullmatch(fields["skillRepositorySource"]):
        raise ValueError("リポジトリの種類は小文字英数字と - で指定してください（例 github）。")
    commit = fields.get("skillCommit", "")
    version = fields.get("skillVersion", "")
    if not commit and not version:
        raise ValueError("固定した commit または version の少なくとも一方を指定してください。")
    if commit and not _SKILL_COMMIT.fullmatch(commit):
        raise ValueError(
            "固定した commit は 40 文字（SHA-1）または 64 文字（SHA-256）の小文字 16 進数で指定してください。"
        )
    if version and (not _SKILL_VERSION.fullmatch(version) or _WILDCARD_VERSION.search(version)):
        raise ValueError(
            "固定した version は、数字（または v と数字）で始まる特定の版で指定してください"
            "（例 1.2.0、v1.2.0。latest・ブランチ名・範囲指定は不可）。"
        )
    digest = fields.get("skillDigest", "")
    if digest and not _SKILL_DIGEST.fullmatch(digest):
        raise ValueError("digest は sha256: に続く 64 文字の小文字 16 進数で指定してください。")


# --- descriptor の種類ごとの検査（不正なら日本語の ValueError。JSON は compact にそろえ、fields を書き換える）


def _check_custom(fields: dict[str, str]) -> list[SecretFinding]:
    # 自由記述の項目だけなので、呼び出し側のフォーム項目の検査（scan_text）で足りる
    return []


def _check_a2a(fields: dict[str, str]) -> list[SecretFinding]:
    _check_version("A2A Agent Card", fields["schemaVersion"], A2A_SCHEMA_VERSIONS)
    card = _json_object("Agent Card", fields["definition"])
    _require_keys("Agent Card", card, _A2A_CARD_REQUIRED)
    fields["definition"] = compact_json(card)
    return scan_json("definition", card)


def _check_mcp(fields: dict[str, str]) -> list[SecretFinding]:
    _check_version("MCP サーバー定義", fields["schemaVersion"], MCP_SERVER_SCHEMA_VERSIONS)
    _check_version("MCP の tools 定義", fields["toolsSchemaVersion"], MCP_TOOLS_SCHEMA_VERSIONS)
    server = _json_object("MCP サーバー定義", fields["definition"])
    _require_keys("MCP サーバー定義", server, _MCP_SERVER_REQUIRED)
    tools = _json_object("MCP の tools 定義", fields["toolsDefinition"])
    _check_tools(tools)
    fields["definition"] = compact_json(server)
    fields["toolsDefinition"] = compact_json(tools)
    return scan_json("definition", server) + scan_json("toolsDefinition", tools)


def _check_skill(fields: dict[str, str]) -> list[SecretFinding]:
    _check_version("Skill の定義", fields["schemaVersion"], SKILL_SCHEMA_VERSIONS)
    _check_skill_pin(fields)
    _check_skill_md(fields["skillMd"])
    return scan_text("skillMd", fields["skillMd"])


# --- descriptor の種類ごとの、CreateRegistryRecord に送る descriptor の中身


def _build_custom(fields: Mapping[str, str]) -> dict[str, Any]:
    return {"data": canonical({key: fields[key] for key in CUSTOM_DEFINITION_FIELDS})}


def _build_a2a(fields: Mapping[str, str]) -> dict[str, Any]:
    return {"data": fields["definition"], "dataSchemaVersion": fields["schemaVersion"]}


def _build_mcp(fields: Mapping[str, str]) -> dict[str, Any]:
    return {
        "data": fields["definition"],
        "dataSchemaVersion": fields["schemaVersion"],
        "additionalData": {
            "tools": {
                "data": fields["toolsDefinition"],
                "dataSchemaVersion": fields["toolsSchemaVersion"],
            }
        },
    }


def _build_skill(fields: Mapping[str, str]) -> dict[str, Any]:
    repository = {"url": fields["skillRepositoryUrl"], "source": fields["skillRepositorySource"]}
    return {
        "data": compact_json({"repository": repository}),
        "dataSchemaVersion": fields["schemaVersion"],
        "additionalData": {"skillMd": {"data": fields["skillMd"]}},
    }


# --- descriptor の種類ごとの、AWS が返した descriptor の中身からの入力項目の読み取り（読めなければ KeyError など）


def _read_custom(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    definition = json.loads(descriptor["data"])
    if not isinstance(definition, Mapping) or set(definition) != set(CUSTOM_DEFINITION_FIELDS):
        raise ValueError("custom descriptor の定義がフォームの項目と一致しません。")
    return dict(definition)


def _read_a2a(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    return {"schemaVersion": descriptor["dataSchemaVersion"], "definition": descriptor["data"]}


def _read_mcp(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    tools = descriptor["additionalData"]["tools"]
    return {
        "schemaVersion": descriptor["dataSchemaVersion"],
        "definition": descriptor["data"],
        "toolsSchemaVersion": tools["dataSchemaVersion"],
        "toolsDefinition": tools["data"],
    }


def _read_skill(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    repository = _json_object("Skill の定義", descriptor["data"])["repository"]
    return {
        "schemaVersion": descriptor["dataSchemaVersion"],
        "skillRepositoryUrl": repository["url"],
        "skillRepositorySource": repository["source"],
        "skillMd": descriptor["additionalData"]["skillMd"]["data"],
    }


@dataclass(frozen=True)
class _DescriptorHandler:
    """descriptor の種類ごとの検査・AWS に送る中身・読み取りと、補助ストアの管理情報に置く項目。"""

    check: Callable[[dict[str, str]], list[SecretFinding]]
    build: Callable[[Mapping[str, str]], dict[str, Any]]
    read: Callable[[Mapping[str, Any]], dict[str, Any]]
    # 補助ストアの管理情報に保存する項目
    supplement_fields: tuple[str, ...]
    # 管理情報の項目を下書きの正本として読むか（custom は descriptor が正本で、管理情報は写し。ADR-0008）
    supplement_is_source: bool


_HANDLERS: Mapping[DescriptorType, _DescriptorHandler] = {
    CUSTOM_DESCRIPTOR_TYPE: _DescriptorHandler(
        _check_custom, _build_custom, _read_custom, SUPPLEMENT_FIELDS, supplement_is_source=False
    ),
    A2A_DESCRIPTOR_TYPE: _DescriptorHandler(
        _check_a2a, _build_a2a, _read_a2a, SUPPLEMENT_FIELDS, supplement_is_source=True
    ),
    MCP_DESCRIPTOR_TYPE: _DescriptorHandler(
        _check_mcp, _build_mcp, _read_mcp, SUPPLEMENT_FIELDS, supplement_is_source=True
    ),
    SKILL_DESCRIPTOR_TYPE: _DescriptorHandler(
        _check_skill, _build_skill, _read_skill, ALL_SUPPLEMENT_FIELDS, supplement_is_source=True
    ),
}


def normalize_and_check(
    descriptor_type: DescriptorType, fields: dict[str, str]
) -> list[SecretFinding]:
    """descriptor ごとの定義を検査し、JSON は compact にそろえる。秘密情報らしき値の検出結果を返す。

    不正な schema version・形式は ValueError（日本語）。fields は書き換える。
    """
    return _HANDLERS[descriptor_type].check(fields)


def supplement_fields(descriptor_type: DescriptorType) -> tuple[str, ...]:
    """補助ストアの管理情報に置く項目。"""
    return _HANDLERS[descriptor_type].supplement_fields


def create_descriptors(
    descriptor_type: DescriptorType, fields: Mapping[str, str]
) -> dict[str, Any]:
    """CreateRegistryRecord の descriptors。primary descriptor を1個だけ含む。"""
    return {descriptor_type: _HANDLERS[descriptor_type].build(fields)}


def _as_patch(value: object) -> dict[str, Any]:
    """CreateRegistryRecord の形を、UpdateRegistryRecord の `optionalValue` で包んだ patch に変える。"""
    if isinstance(value, Mapping):
        return {"optionalValue": {key: _as_patch(child) for key, child in value.items()}}
    return {"optionalValue": value}


def update_descriptors(
    descriptor_type: DescriptorType, fields: Mapping[str, str]
) -> dict[str, Any]:
    """UpdateRegistryRecord の descriptors に入れる patch。

    data・dataSchemaVersion・additionalData を個別に更新する。
    """
    return {descriptor_type: _as_patch(_HANDLERS[descriptor_type].build(fields))}


def read_descriptor_fields(
    descriptor_type: DescriptorType, descriptor: Mapping[str, Any], supplement: Mapping[str, Any]
) -> dict[str, Any]:
    """AWS が返した descriptor の中身と補助ストアの管理情報から、下書きの入力項目を読む。

    descriptor に置けない項目は管理情報を正本として読む（ADR-0009）。読めなければ KeyError など。
    """
    handler = _HANDLERS[descriptor_type]
    fields = handler.read(descriptor)
    if handler.supplement_is_source:
        fields |= {
            key: supplement.get(key, "") if key in OPTIONAL_FIELDS else supplement[key]
            for key in handler.supplement_fields
        }
    return fields


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
