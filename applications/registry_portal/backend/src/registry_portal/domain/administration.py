"""取り込み・所有変更の入力と、Admin API の応答型。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from registry_portal.domain.descriptors import (
    ALL_SUPPLEMENT_FIELDS,
    check_kind,
    read_descriptor_fields,
)
from registry_portal.domain.drafts import Management, RecordStatus, digest
from registry_portal.domain.catalog import CatalogRecordType
from registry_portal.domain.submissions import REASON_MAX_LENGTH, SNAPSHOT_RECORD_FIELDS


class AdminRecordSummary(TypedDict):
    recordId: str
    name: str
    displayName: str
    recordType: CatalogRecordType
    recordVersion: str
    status: RecordStatus
    managed: bool
    ownerTeam: str | None
    managementHash: str | None


class Connected(TypedDict):
    state: Literal["CONNECTED"]


class ConnectionFailure(TypedDict):
    state: Literal["ERROR"]
    code: str
    message: str


class AutoApproval(TypedDict):
    state: Literal["DISABLED", "ENABLED", "UNKNOWN"]


class ConnectionReport(TypedDict):
    registryArn: str
    region: str
    authorization: Literal["CUSTOM_JWT", "AWS_IAM"] | None
    connection: Connected | ConnectionFailure
    autoApproval: AutoApproval
    warnings: list[str]


class AdministrationResult(TypedDict):
    recordId: str
    ownerTeam: str
    operationId: str
    operationState: Literal["SUCCEEDED"]
    publishedVersion: str | None
    a2aBackfillRequired: bool


@dataclass(frozen=True)
class OwnershipInput:
    owner_team: str
    reason: str
    expected_hash: str | None

    @classmethod
    def parse(cls, body: Mapping[str, Any], *, reassign: bool) -> OwnershipInput:
        allowed = {"ownerTeam", "reason"} | ({"expectedManagementHash"} if reassign else set())
        if set(body) != allowed:
            raise ValueError(
                "所有チーム・理由と、所有変更では取得時の管理情報ハッシュを指定してください。"
            )
        team, reason = body.get("ownerTeam"), body.get("reason")
        if not isinstance(team, str) or not re.fullmatch(r"team-[A-Za-z0-9_./-]{1,123}", team):
            raise ValueError(
                "所有チームは team- で始まる128文字以内のグループ名を指定してください。"
            )
        if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_MAX_LENGTH:
            raise ValueError(f"理由は1〜{REASON_MAX_LENGTH}文字で指定してください。")
        expected = body.get("expectedManagementHash")
        if reassign and (
            not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)
        ):
            raise ValueError("取得時の管理情報ハッシュを指定してください。")
        return cls(team, reason.strip(), expected)


def management_hash(management: Management) -> str:
    return digest(dict(management.item))


def adopted_supplement(record: Mapping[str, Any]) -> dict[str, str]:
    """定義から読める補助情報だけを写し、存在しない値を推測しない。"""
    try:
        descriptors = record["descriptors"]
        if not isinstance(descriptors, Mapping) or len(descriptors) != 1:
            raise ValueError("取り込みには primary descriptor が1個必要です。")
        _, kind = check_kind(record["recordType"], next(iter(descriptors)))
        fields = read_descriptor_fields(
            kind, descriptors[kind], dict.fromkeys(ALL_SUPPLEMENT_FIELDS, "")
        )
        return {key: fields.get(key, "") for key in ALL_SUPPLEMENT_FIELDS}
    except (KeyError, TypeError, StopIteration):
        raise ValueError(
            "Registry の定義を読み取れません。対応する descriptor を確認してください。"
        ) from None


def adopted_snapshot(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **adopted_supplement(record),
        "relatedReferences": [],
        "record": {key: record[key] for key in SNAPSHOT_RECORD_FIELDS if key in record},
    }
