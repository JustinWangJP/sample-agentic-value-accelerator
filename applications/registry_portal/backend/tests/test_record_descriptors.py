"""A2A・MCP サーバー・Skill の登録（Seam 1: 管理 Lambda の入出力）。

種別ごとに AWS が受理する primary descriptor を1個だけ送ること、`isA2A` を descriptor から導出すること、
不正な schema version と種別・descriptor の組合せを AWS に送る前に日本語で拒否することを、
HTTP 応答・Registry への呼び出し・補助テーブルの最終状態で確認する。
"""

import pytest
from botocore.stub import Stubber
from moto import mock_aws

from draft_helpers import RECORD_ID, call, clients, event, handler_for, management_item
from record_fixtures import (
    A2A_DRAFT,
    A2A_DESCRIPTORS,
    CASES,
    MCP_DESCRIPTORS,
    MCP_DRAFT,
    METADATA,
    SKILL_DESCRIPTORS,
    SKILL_DRAFT,
    COMMIT,
    CUSTOM_DESCRIPTORS,
    CUSTOM_DRAFT,
    DIGEST,
    stub_create,
)


@pytest.mark.parametrize("kind", CASES)
@mock_aws
def test_each_kind_sends_exactly_one_primary_descriptor_that_aws_accepts(kind):
    draft, descriptors, metadata = CASES[kind]
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, draft, descriptors, metadata)
        status, created = call(handler_for(registry, db), event(body=draft))
        assert status == 201
        assert created["draft"] == draft
        assert created["recordId"] == RECORD_ID
        stub.assert_no_pending_responses()


@mock_aws
def test_custom_agent_is_not_flagged_as_a2a():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, {**METADATA, "isA2A": False})
        status, _ = call(handler_for(registry, db), event(body=CUSTOM_DRAFT))
        assert status == 201
        stub.assert_no_pending_responses()


@mock_aws
def test_record_type_alone_defaults_to_the_descriptor_aws_accepts_for_it():
    draft = {key: value for key, value in MCP_DRAFT.items() if key != "descriptorType"}
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, MCP_DRAFT, MCP_DESCRIPTORS, METADATA)
        status, created = call(handler_for(registry, db), event(body=draft))
        assert status == 201
        assert created["draft"]["descriptorType"] == "mcpServer"
        stub.assert_no_pending_responses()


@mock_aws
def test_supplement_keeps_contact_terms_and_connection_reference():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, A2A_DRAFT, A2A_DESCRIPTORS, {**METADATA, "isA2A": True})
        call(handler_for(registry, db), event(body=A2A_DRAFT))
        item = management_item(db)
        assert item["contact"] == {"S": "payments@example.com"}
        assert item["termsOfUse"] == {"S": "社内利用のみ"}
        assert item["connectionReference"] == {"S": "https://example.com/payments"}
        assert "skillCommit" not in item


@mock_aws
def test_supplement_keeps_the_skill_pin_beside_contact():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, SKILL_DRAFT, SKILL_DESCRIPTORS, METADATA)
        call(handler_for(registry, db), event(body=SKILL_DRAFT))
        item = management_item(db)
        assert item["skillCommit"] == {"S": COMMIT}
        assert item["skillVersion"] == {"S": "1.2.0"}
        assert item["skillDigest"] == {"S": DIGEST}
        assert item["contact"] == {"S": "payments@example.com"}
