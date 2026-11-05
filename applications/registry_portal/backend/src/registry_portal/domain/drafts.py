from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from registry_portal.domain.catalog import DATA_CLASSIFICATIONS

DEFINITION_FIELDS = (
    "purpose",
    "connectionReference",
    "authentication",
    "contact",
    "termsOfUse",
)
TEXT_LIMITS = {
    "name": 255,
    "displayName": 255,
    "description": 4096,
    "recordVersion": 255,
    "ownerTeam": 128,
    "environment": 3,
    "dataClassification": 7,
    "purpose": 4096,
    "connectionReference": 2048,
    "authentication": 1024,
    "contact": 1024,
    "termsOfUse": 4096,
}
SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def content_hash(record: Mapping[str, Any], management: Mapping[str, Any] | None = None) -> str:
    content = {
        key: record.get(key)
        for key in (
            "name",
            "displayName",
            "description",
            "recordType",
            "recordVersion",
            "descriptors",
            "customMetadata",
        )
    }
    if management is not None:
        content["supplement"] = {
            key: management.get(key)
            for key in ("ownerTeam", "contact", "termsOfUse", "connectionReference")
        }
    return digest(content)


@dataclass(frozen=True)
class AgentDraft:
    fields: Mapping[str, str]

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> AgentDraft:
        if set(value) - TEXT_LIMITS.keys():
            raise ValueError("下書きに指定できない項目が含まれています。")
        fields = {"environment": "dev", **value}
        for key, limit in TEXT_LIMITS.items():
            text = fields.get(key)
            if not isinstance(text, str) or not text.strip() or len(text) > limit:
                raise ValueError(f"{key} は1〜{limit}文字で指定してください。")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_./-]*", fields["name"]):
            raise ValueError("name は英数字で始め、英数字と _ . / - で指定してください。")
        if not SEMVER.fullmatch(fields["recordVersion"]):
            raise ValueError("recordVersion は semver（例 1.0.0）で指定してください。")
        if fields["environment"] != "dev":
            raise ValueError("environment は dev のみ指定できます。")
        if fields["dataClassification"] not in DATA_CLASSIFICATIONS:
            raise ValueError("データ分類は public または private を指定してください。")
        return cls(fields)

    def create_payload(self) -> dict[str, Any]:
        return {
            **{
                key: self.fields[key]
                for key in ("name", "displayName", "description", "recordVersion")
            },
            "recordType": "AGENT",
            "descriptors": {
                "custom": {"data": canonical({key: self.fields[key] for key in DEFINITION_FIELDS})}
            },
            "customMetadata": {
                **{
                    key: self.fields[key]
                    for key in ("ownerTeam", "environment", "dataClassification")
                },
                "isA2A": False,
            },
        }

    def update_payload(self) -> dict[str, Any]:
        create = self.create_payload()
        return {
            "displayName": {"optionalValue": self.fields["displayName"]},
            "description": {"optionalValue": self.fields["description"]},
            "recordVersion": self.fields["recordVersion"],
            "customMetadata": {"optionalValue": create["customMetadata"]},
            "descriptors": {
                "optionalValue": {
                    "custom": {
                        "optionalValue": {
                            "data": {"optionalValue": create["descriptors"]["custom"]["data"]},
                        }
                    }
                }
            },
        }


def version_is_greater(candidate: str, published: str) -> bool:
    def parts(value: str) -> tuple[tuple[int, int, int], list[str]]:
        match = SEMVER.fullmatch(value)
        if match is None:
            raise ValueError("公開中の recordVersion を比較できません。管理者に連絡してください。")
        return (int(match[1]), int(match[2]), int(match[3])), (
            (match[4] or "").split(".") if match[4] else []
        )

    left, lp = parts(candidate)
    right, rp = parts(published)
    if left != right:
        return left > right
    if not lp or not rp:
        return bool(rp) and not lp
    for a, b in zip(lp, rp):
        if a == b:
            continue
        if a.isdigit() and b.isdigit():
            return int(a) > int(b)
        if a.isdigit() != b.isdigit():
            return not a.isdigit()
        return a > b
    return len(lp) > len(rp)
