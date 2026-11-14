"""A2A・MCP サーバー・Skill の編集・読み取り・確認（Seam 1: 管理 Lambda の入出力）。

descriptor を切り替える更新の payload と `isA2A` の更新、AWS が受理後に補う項目があっても結果を確定できること、
読み取りと一覧が種別をまたいで動くことを確認する。
"""

import json

import pytest
from botocore.stub import Stubber
from moto import mock_aws

from catalog_helpers import REGISTRY_ARN
from draft_helpers import RECORD_ID, aws_record, call, clients, event, handler_for, management_item
from record_fixtures import (
    A2A_DESCRIPTORS,
    A2A_DRAFT,
    CARD,
    CASES,
    CUSTOM_DESCRIPTORS,
    CUSTOM_DRAFT,
    MCP_DESCRIPTORS,
    MCP_DRAFT,
    METADATA,
    SKILL_DESCRIPTORS,
    SKILL_DRAFT,
    compact,
    stub_create,
)

GET = {"registryId": REGISTRY_ARN, "recordId": RECORD_ID}


def patch(handler, created, draft):
    return call(
        handler,
        event(
            "PATCH",
            f"/api/records/{RECORD_ID}",
            {"draft": draft, "expectedContentHash": created["contentHash"]},
        ),
    )


def stub_update(stub, before, expected, after):
    """更新前の取得、更新、更新後の確認の取得を順に登録する。"""
    stub.add_response("get_registry_record", before, GET)
    stub.add_response(
        "update_registry_record",
        {**after, "status": "UPDATING"},
        {"registryId": REGISTRY_ARN, "recordId": RECORD_ID, **expected},
    )
    stub.add_response("get_registry_record", after, GET)


def patch_value(value):
    return {"optionalValue": value}


@mock_aws
def test_switching_custom_to_a2a_sets_isa2a_and_deletes_the_old_descriptor():
    registry, db = clients()
    custom_metadata = {**METADATA, "isA2A": False}
    a2a_metadata = {**METADATA, "isA2A": True}
    with Stubber(registry) as stub:
        stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, custom_metadata)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=CUSTOM_DRAFT))
        stub_update(
            stub,
            aws_record(
                CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=custom_metadata
            ),
            {
                "displayName": patch_value(A2A_DRAFT["displayName"]),
                "description": patch_value(A2A_DRAFT["description"]),
                "recordVersion": "1.0.0",
                "customMetadata": patch_value(a2a_metadata),
                "descriptors": patch_value(
                    {
                        "custom": {},
                        "a2aAgentCard": patch_value(
                            {
                                "data": patch_value(compact(CARD)),
                                "dataSchemaVersion": patch_value("0.3"),
                            }
                        ),
                    }
                ),
            },
            aws_record(A2A_DRAFT, descriptors=A2A_DESCRIPTORS, customMetadata=a2a_metadata),
        )
        status, updated = patch(handler, created, A2A_DRAFT)
        assert status == 200
        assert updated["draft"]["descriptorType"] == "a2aAgentCard"
        stub.assert_no_pending_responses()


@mock_aws
def test_switching_a2a_back_to_custom_clears_isa2a():
    registry, db = clients()
    custom_metadata = {**METADATA, "isA2A": False}
    a2a_metadata = {**METADATA, "isA2A": True}
    with Stubber(registry) as stub:
        stub_create(stub, A2A_DRAFT, A2A_DESCRIPTORS, a2a_metadata)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=A2A_DRAFT))
        stub_update(
            stub,
            aws_record(A2A_DRAFT, descriptors=A2A_DESCRIPTORS, customMetadata=a2a_metadata),
            {
                "displayName": patch_value(CUSTOM_DRAFT["displayName"]),
                "description": patch_value(CUSTOM_DRAFT["description"]),
                "recordVersion": "1.0.0",
                "customMetadata": patch_value(custom_metadata),
                "descriptors": patch_value(
                    {
                        "a2aAgentCard": {},
                        "custom": patch_value(
                            {"data": patch_value(CUSTOM_DESCRIPTORS["custom"]["data"])}
                        ),
                    }
                ),
            },
            aws_record(
                CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=custom_metadata
            ),
        )
        status, _ = patch(handler, created, CUSTOM_DRAFT)
        assert status == 200
        stub.assert_no_pending_responses()


@mock_aws
def test_updating_a_portal_record_replaces_metadata_without_keeping_unknown_keys():
    """既存 metadata を残すのは取り込みレコードだけ。ポータルで作成したレコードはポータルの項目で置き換える。"""
    registry, db = clients()
    custom_metadata = {**METADATA, "isA2A": False}
    with Stubber(registry) as stub:
        stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, custom_metadata)
        handler = handler_for(registry, db)
        call(handler, event(body=CUSTOM_DRAFT))
        before = aws_record(
            CUSTOM_DRAFT,
            descriptors=CUSTOM_DESCRIPTORS,
            customMetadata={**custom_metadata, "external": "ポータル外で追加"},
        )
        stub.add_response("get_registry_record", before, GET)
        status, view = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
        assert status == 200
        stub_update(
            stub,
            before,
            {
                "displayName": patch_value(CUSTOM_DRAFT["displayName"]),
                "description": patch_value(CUSTOM_DRAFT["description"]),
                "recordVersion": "1.0.0",
                "customMetadata": patch_value(custom_metadata),
                "descriptors": patch_value(
                    {
                        "custom": patch_value(
                            {"data": patch_value(CUSTOM_DESCRIPTORS["custom"]["data"])}
                        )
                    }
                ),
            },
            aws_record(
                CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=custom_metadata
            ),
        )
        status, _ = patch(handler, view, CUSTOM_DRAFT)
        assert status == 200
        stub.assert_no_pending_responses()


@mock_aws
def test_updating_mcp_patches_server_and_tools_fields_individually():
    registry, db = clients()
    changed = {**MCP_DRAFT, "toolsSchemaVersion": "2025-06-18", "description": "新しい説明"}
    changed_descriptors = {
        "mcpServer": {
            **MCP_DESCRIPTORS["mcpServer"],
            "additionalData": {
                "tools": {
                    **MCP_DESCRIPTORS["mcpServer"]["additionalData"]["tools"],
                    "dataSchemaVersion": "2025-06-18",
                }
            },
        }
    }
    with Stubber(registry) as stub:
        stub_create(stub, MCP_DRAFT, MCP_DESCRIPTORS, METADATA)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=MCP_DRAFT))
        server = MCP_DESCRIPTORS["mcpServer"]
        stub_update(
            stub,
            aws_record(MCP_DRAFT, descriptors=MCP_DESCRIPTORS, customMetadata=METADATA),
            {
                "displayName": patch_value(MCP_DRAFT["displayName"]),
                "description": patch_value("新しい説明"),
                "recordVersion": "1.0.0",
                "customMetadata": patch_value(METADATA),
                "descriptors": patch_value(
                    {
                        "mcpServer": patch_value(
                            {
                                "data": patch_value(server["data"]),
                                "dataSchemaVersion": patch_value(server["dataSchemaVersion"]),
                                "additionalData": patch_value(
                                    {
                                        "tools": patch_value(
                                            {
                                                "data": patch_value(
                                                    server["additionalData"]["tools"]["data"]
                                                ),
                                                "dataSchemaVersion": patch_value("2025-06-18"),
                                            }
                                        )
                                    }
                                ),
                            }
                        )
                    }
                ),
            },
            aws_record(
                changed,
                description="新しい説明",
                descriptors=changed_descriptors,
                customMetadata=METADATA,
            ),
        )
        status, updated = patch(handler, created, changed)
        assert status == 200
        assert updated["draft"] == changed
        stub.assert_no_pending_responses()


@mock_aws
def test_updating_skill_replaces_skill_md_and_the_pin_in_the_supplement():
    registry, db = clients()
    new_commit = "f" * 40
    new_md = "---\nname: payments-skill\ndescription: 更新\n---\n新しい手順\n"
    changed = {**SKILL_DRAFT, "skillCommit": new_commit, "skillVersion": "1.3.0", "skillMd": new_md}
    skill = SKILL_DESCRIPTORS["agentSkillsDefinition"]
    changed_descriptors = {
        "agentSkillsDefinition": {**skill, "additionalData": {"skillMd": {"data": new_md}}}
    }
    with Stubber(registry) as stub:
        stub_create(stub, SKILL_DRAFT, SKILL_DESCRIPTORS, METADATA)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=SKILL_DRAFT))
        stub_update(
            stub,
            aws_record(SKILL_DRAFT, descriptors=SKILL_DESCRIPTORS, customMetadata=METADATA),
            {
                "displayName": patch_value(SKILL_DRAFT["displayName"]),
                "description": patch_value(SKILL_DRAFT["description"]),
                "recordVersion": "1.0.0",
                "customMetadata": patch_value(METADATA),
                "descriptors": patch_value(
                    {
                        "agentSkillsDefinition": patch_value(
                            {
                                "data": patch_value(skill["data"]),
                                "dataSchemaVersion": patch_value("0.1.0"),
                                "additionalData": patch_value(
                                    {
                                        "skillMd": patch_value({"data": patch_value(new_md)}),
                                    }
                                ),
                            }
                        )
                    }
                ),
            },
            aws_record(changed, descriptors=changed_descriptors, customMetadata=METADATA),
        )
        status, updated = patch(handler, created, changed)
        assert status == 200
        assert updated["draft"] == changed
        item = management_item(db)
        assert item["skillCommit"] == {"S": new_commit}
        assert item["skillVersion"] == {"S": "1.3.0"}
        stub.assert_no_pending_responses()


@mock_aws
def test_record_type_cannot_change_on_update_and_the_lock_is_released():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, MCP_DRAFT, MCP_DESCRIPTORS, METADATA)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=MCP_DRAFT))
        stub.add_response(
            "get_registry_record",
            aws_record(MCP_DRAFT, descriptors=MCP_DESCRIPTORS, customMetadata=METADATA),
            GET,
        )
        status, body = patch(handler, created, {**A2A_DRAFT, "name": MCP_DRAFT["name"]})
        assert status == 400
        assert "種別は作成後に変更できません" in body["error"]["message"]
        stub.assert_no_pending_responses()


def reformatted(descriptors):
    """AWS が data を整形し直し、source を補って返した場合の descriptor。"""
    card = json.loads(descriptors["a2aAgentCard"]["data"])
    return {
        "a2aAgentCard": {
            **descriptors["a2aAgentCard"],
            "data": json.dumps(dict(reversed(card.items())), ensure_ascii=False, indent=2),
        }
    }


@mock_aws
def test_creation_is_confirmed_even_if_aws_reformats_the_definition_and_adds_metadata():
    registry, db = clients()
    returned = aws_record(
        A2A_DRAFT,
        descriptors=reformatted(A2A_DESCRIPTORS),
        customMetadata={**METADATA, "isA2A": True},
    )
    with Stubber(registry) as stub:
        stub_create(stub, A2A_DRAFT, A2A_DESCRIPTORS, {**METADATA, "isA2A": True}, returned)
        status, created = call(handler_for(registry, db), event(body=A2A_DRAFT))
        assert status == 201
        assert created["operationState"] == "SUCCEEDED"
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "difference",
    [
        {"customMetadata": {**METADATA, "isA2A": False}},
        {"descriptors": CUSTOM_DESCRIPTORS},
        {"descriptors": {**A2A_DESCRIPTORS, "custom": CUSTOM_DESCRIPTORS["custom"]}},
        {"description": "別の説明"},
    ],
)
@mock_aws
def test_creation_stays_unconfirmed_while_aws_returns_different_content(difference):
    registry, db = clients()
    returned = aws_record(
        A2A_DRAFT,
        descriptors=A2A_DESCRIPTORS,
        customMetadata={**METADATA, "isA2A": True},
    )
    with Stubber(registry) as stub:
        stub_create(
            stub,
            A2A_DRAFT,
            A2A_DESCRIPTORS,
            {**METADATA, "isA2A": True},
            {**returned, **difference},
        )
        status, body = call(handler_for(registry, db), event(body=A2A_DRAFT))
        assert status == 202
        assert body["operationState"] == "VERIFYING"


@pytest.mark.parametrize("kind", CASES)
@mock_aws
def test_each_kind_can_be_read_back_as_a_form_draft(kind):
    draft, descriptors, metadata = CASES[kind]
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, draft, descriptors, metadata)
        handler = handler_for(registry, db)
        _, created = call(handler, event(body=draft))
        stub.add_response(
            "get_registry_record",
            aws_record(draft, descriptors=descriptors, customMetadata=metadata),
            GET,
        )
        status, read = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
        assert status == 200
        assert read["draft"] == draft
        assert read["contentHash"] == created["contentHash"]
        stub.assert_no_pending_responses()


@mock_aws
def test_a_record_with_two_primary_descriptors_is_reported_instead_of_overwritten():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, A2A_DRAFT, A2A_DESCRIPTORS, {**METADATA, "isA2A": True})
        handler = handler_for(registry, db)
        call(handler, event(body=A2A_DRAFT))
        stub.add_response(
            "get_registry_record",
            aws_record(
                A2A_DRAFT,
                descriptors={**A2A_DESCRIPTORS, "custom": CUSTOM_DESCRIPTORS["custom"]},
                customMetadata={**METADATA, "isA2A": True},
            ),
            GET,
        )
        status, body = call(handler, event("GET", f"/api/records/{RECORD_ID}"))
        assert status == 502
        assert "フォームで読み取れません" in body["error"]["message"]


@mock_aws
def test_list_includes_mcp_and_skill_records_with_their_record_type():
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, MCP_DRAFT, MCP_DESCRIPTORS, METADATA)
        handler = handler_for(registry, db)
        call(handler, event(body=MCP_DRAFT))
        summary = {
            key: value
            for key, value in aws_record(MCP_DRAFT).items()
            if key not in ("descriptors", "customMetadata")
        }
        stub.add_response(
            "list_registry_records",
            {"registryRecords": [summary]},
            {
                "registryId": REGISTRY_ARN,
                "maxResults": 20,
                "filters": [{"name": "recordType", "values": ["AGENT", "MCP", "SKILL"]}],
            },
        )
        status, page = call(handler, event("GET", "/api/records"))
        assert status == 200
        assert [item["recordType"] for item in page["items"]] == ["MCP"]
        stub.assert_no_pending_responses()
