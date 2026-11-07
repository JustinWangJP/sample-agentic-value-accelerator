"""A2A・MCP サーバー・Skill の入力の検証（Seam 1: 管理 Lambda の入出力）。

不正な入力は AWS を呼ぶ前に、日本語のメッセージで 400 になる。環境・データ分類・schema version・
種別と descriptor の組合せ・Skill の固定情報・秘密情報らしき値を確認する。
"""

import json
import re

import pytest
from botocore.stub import Stubber
from moto import mock_aws

from draft_helpers import call, clients, event, handler_for, management_item
from record_fixtures import (
    A2A_DRAFT,
    CASES,
    MCP_DESCRIPTORS,
    MCP_DRAFT,
    SKILL_DESCRIPTORS,
    SKILL_DRAFT,
    METADATA,
    compact,
    stub_create,
)

JAPANESE = re.compile("[぀-ヿ一-鿿]")


def rejected(draft):
    """AWS を呼ばずに 400 で拒否され、メッセージが日本語であることを確認して、メッセージを返す。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        status, body = call(handler_for(registry, db), event(body=draft))
        stub.assert_no_pending_responses()
    assert status == 400
    assert body["error"]["code"] == "VALIDATION_ERROR"
    message = body["error"]["message"]
    assert JAPANESE.search(message)
    return message


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"recordType": "MCP", "descriptorType": "a2aAgentCard"}, "種別 MCP に指定できる"),
        ({"recordType": "AGENT", "descriptorType": "mcpServer"}, "種別 AGENT に指定できる"),
        ({"recordType": "AGENT", "descriptorType": "agentSkillsDefinition"}, "種別 AGENT に"),
        ({"recordType": "SKILL", "descriptorType": "custom"}, "種別 SKILL に指定できる"),
        ({"recordType": "GATEWAY"}, "recordType は"),
        ({"descriptorType": "http"}, "指定できる descriptor は"),
    ],
)
@mock_aws
def test_kind_and_descriptor_mismatch_is_a_japanese_error_without_calling_aws(change, expected):
    assert expected in rejected({**A2A_DRAFT, **change})


@pytest.mark.parametrize(
    ("base", "change"),
    [
        (A2A_DRAFT, {"schemaVersion": "0.2"}),
        (A2A_DRAFT, {"schemaVersion": "0.3.0"}),
        (MCP_DRAFT, {"schemaVersion": "2025-11-25"}),
        (MCP_DRAFT, {"toolsSchemaVersion": "2025-12-11"}),
        (SKILL_DRAFT, {"schemaVersion": "1.0.0"}),
    ],
)
@mock_aws
def test_unsupported_schema_version_is_rejected_before_aws(base, change):
    assert "schema version" in rejected({**base, **change})


@pytest.mark.parametrize(
    ("base", "change"),
    [
        (A2A_DRAFT, {"definition": "{not json"}),
        (A2A_DRAFT, {"definition": "[]"}),
        (A2A_DRAFT, {"definition": "NaN"}),
        (A2A_DRAFT, {"definition": "{" + " " * 24000 + "}"}),
        (A2A_DRAFT, {"definition": compact({"name": "x"})}),
        (MCP_DRAFT, {"definition": compact({"name": "x"})}),
        (MCP_DRAFT, {"toolsDefinition": compact({"tools": []})}),
        (MCP_DRAFT, {"toolsDefinition": compact({"tools": [{"name": "t"}]})}),
        (SKILL_DRAFT, {"skillMd": "# frontmatter なし"}),
        (SKILL_DRAFT, {"skillMd": "---\nname: x\n---\n"}),
    ],
)
@mock_aws
def test_malformed_definition_is_rejected_before_aws(base, change):
    rejected({**base, **change})


@pytest.mark.parametrize(
    "change",
    [
        {"skillRepositoryUrl": "http://github.com/example/skill"},
        {"skillRepositoryUrl": "https://user:pass@github.com/example/skill"},
        {"skillRepositoryUrl": "https://github.com/example/skill?token=abcdef123456"},
        {"skillCommit": "main"},
        {"skillCommit": "0123456"},
        {"skillCommit": "G" * 40},
        {"skillVersion": "latest"},
        {"skillVersion": "main"},
        {"skillVersion": "^1.2.0"},
        {"skillVersion": "1.x"},
        {"skillDigest": "md5:abcd"},
        {"skillDigest": "sha256:ABC"},
        {"skillRepositorySource": "GitHub"},
    ],
)
@mock_aws
def test_skill_must_pin_repository_commit_and_version_and_digest_if_given(change):
    rejected({**SKILL_DRAFT, **change})


@mock_aws
def test_skill_digest_is_optional_and_kept_empty_when_unknown():
    draft = {key: value for key, value in SKILL_DRAFT.items() if key != "skillDigest"}
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, draft, SKILL_DESCRIPTORS, METADATA)
        status, created = call(handler_for(registry, db), event(body=draft))
        assert status == 201
        assert created["draft"] == {**draft, "skillDigest": ""}
        assert management_item(db)["skillDigest"] == {"S": ""}
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("base", [A2A_DRAFT, MCP_DRAFT, SKILL_DRAFT])
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"dataClassification": "confidential"}, ["public", "private"]),
        ({"dataClassification": "secret"}, ["public", "private"]),
        ({"environment": "prod"}, ["dev"]),
        ({"environment": "stg"}, ["dev"]),
        ({"isA2A": "true"}, ["指定できない項目"]),
    ],
)
@mock_aws
def test_common_metadata_rules_apply_to_every_kind(base, change, expected):
    message = rejected({**base, **change})
    # 許可値を案内する。文字数の誤った案内（「1〜7文字」など）にしない
    assert all(value in message for value in expected)
    assert "文字" not in message


@pytest.mark.parametrize("base", [A2A_DRAFT, MCP_DRAFT, SKILL_DRAFT])
@pytest.mark.parametrize("field", ["environment", "dataClassification"])
@mock_aws
def test_empty_choice_value_lists_the_allowed_values(base, field):
    assert "文字" not in rejected({**base, field: ""})


@mock_aws
def test_over_long_text_still_reports_the_character_limit():
    assert "1〜255文字" in rejected({**A2A_DRAFT, "displayName": "あ" * 256})


@pytest.mark.parametrize(
    "skill_md",
    [
        "---\nname: payments\ndescription:\n  決済の照会を行う Skill\n---\n# 本文\n",
        "---\nname: payments\ndescription: >\n  決済の照会\n  を行う\n---\n# 本文\n",
        "---\nname:\n  payments\ndescription: 照会\n---\n# 本文\n",
    ],
)
@mock_aws
def test_skill_md_accepts_values_written_on_the_next_indented_line(skill_md):
    draft = {**SKILL_DRAFT, "skillMd": skill_md}
    descriptors = {
        "agentSkillsDefinition": {
            **SKILL_DESCRIPTORS["agentSkillsDefinition"],
            "additionalData": {"skillMd": {"data": skill_md}},
        }
    }
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, draft, descriptors, METADATA)
        status, _ = call(handler_for(registry, db), event(body=draft))
        assert status == 201
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "skill_md",
    [
        "---\nname: payments\ndescription:\n---\n# 本文\n",
        "---\nname: payments\ndescription:\nlicense: MIT\n---\n# 本文\n",
        "---\nname: payments\ndescription:   \n  \n---\n# 本文\n",
    ],
)
@mock_aws
def test_skill_md_still_rejects_a_key_without_any_value(skill_md):
    assert "frontmatter" in rejected({**SKILL_DRAFT, "skillMd": skill_md})


@pytest.mark.parametrize("kind", CASES)
@mock_aws
def test_environment_defaults_to_dev_for_every_kind(kind):
    draft, descriptors, metadata = CASES[kind]
    without_environment = {key: value for key, value in draft.items() if key != "environment"}
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, draft, descriptors, metadata)
        status, created = call(handler_for(registry, db), event(body=without_environment))
        assert status == 201
        assert created["draft"]["environment"] == "dev"
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("classification", ["public", "private"])
@mock_aws
def test_public_and_private_are_accepted_for_every_kind(classification):
    draft, descriptors, metadata = CASES["mcp"]
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(
            stub,
            {**draft, "dataClassification": classification},
            descriptors,
            {**metadata, "dataClassification": classification},
        )
        status, _ = call(
            handler_for(registry, db), event(body={**draft, "dataClassification": classification})
        )
        assert status == 201
        stub.assert_no_pending_responses()


SECRETS = [
    (
        "description",
        "token は eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123_-x",
        "JWT",
    ),
    ("contact", "AKIAIOSFODNN7EXAMPLE", "AWS アクセスキー"),
    ("termsOfUse", "-----BEGIN RSA PRIVATE KEY-----", "秘密鍵"),
    ("termsOfUse", "Authorization: Bearer abcdefghijklmnop1234567890", "Bearer トークン"),
    ("connectionReference", "https://alice:s3cretpw@example.com/x", "URL に埋め込まれた資格情報"),
    ("connectionReference", "https://example.com/x?api_key=abcd1234efgh", "URL のクエリの資格情報"),
    ("description", "password=hunter2hunter2 を使う", "資格情報の代入"),
]


@pytest.mark.parametrize(("field", "value", "kind"), SECRETS)
@mock_aws
def test_secret_looking_value_in_a_form_field_is_rejected_without_echoing_it(field, value, kind):
    message = rejected({**A2A_DRAFT, field: value})
    assert field in message
    assert kind in message
    assert value not in message
    assert "Secrets Manager" in message


@mock_aws
def test_secret_in_custom_agent_specific_fields_is_rejected():
    custom = {
        "name": "payments-custom",
        "recordType": "AGENT",
        "descriptorType": "custom",
        "displayName": "決済",
        "description": "決済",
        "recordVersion": "1.0.0",
        "ownerTeam": "team-payments",
        "dataClassification": "private",
        "connectionReference": "https://example.com/payments",
        "contact": "payments@example.com",
        "termsOfUse": "社内利用のみ",
        "purpose": "照会",
        "authentication": "Bearer abcdefghijklmnop1234567890",
    }
    message = rejected(custom)
    assert "authentication" in message
    assert "abcdefghijklmnop" not in message


@pytest.mark.parametrize(
    "card_change",
    [
        {"securityToken": "abcdef0123456789abcdef"},
        {"headers": {"Authorization": "Bearer abcdefghijklmnop1234567890"}},
        {"auth": {"password": "hunter2hunter2"}},
        {"skills": [{"id": "a", "name": "a", "description": "api_key: AKIAIOSFODNN7EXAMPLE"}]},
    ],
)
@mock_aws
def test_secret_inside_agent_card_json_is_rejected_with_its_path_only(card_change):
    card = {**json.loads(A2A_DRAFT["definition"]), **card_change}
    message = rejected({**A2A_DRAFT, "definition": compact(card)})
    assert "definition" in message
    assert "hunter2" not in message and "abcdefghijklmnop" not in message
    assert "AKIAIOSFODNN7EXAMPLE" not in message


@mock_aws
def test_secret_in_mcp_tools_definition_is_rejected():
    tools = compact(
        {
            "tools": [
                {
                    "name": "t",
                    "inputSchema": {"type": "object"},
                    "annotations": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz"},
                }
            ]
        }
    )
    assert "toolsDefinition" in rejected({**MCP_DRAFT, "toolsDefinition": tools})


@mock_aws
def test_secret_in_skill_md_is_rejected():
    md = SKILL_DRAFT["skillMd"] + "\nexport TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
    assert "skillMd" in rejected({**SKILL_DRAFT, "skillMd": md})


@mock_aws
def test_references_and_unfilled_secret_declarations_are_not_secrets():
    server = {
        "name": "io.example/payments",
        "description": "決済 MCP サーバー",
        "version": "1.0.0",
        "environmentVariables": [
            {"name": "API_KEY", "isSecret": True, "description": "Secrets Manager から注入"},
            {"name": "TOKEN_ARN", "value": "arn:aws:secretsmanager:us-east-1:1:secret:x"},
        ],
        "headers": [{"name": "Authorization", "value": "${PAYMENTS_AUTHORIZATION}"}],
        "apiKey": "${PAYMENTS_API_KEY}",
    }
    draft = {**MCP_DRAFT, "definition": compact(server)}
    descriptors = {
        "mcpServer": {
            **MCP_DESCRIPTORS["mcpServer"],
            "data": compact(server),
        }
    }
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(stub, draft, descriptors, METADATA)
        status, _ = call(handler_for(registry, db), event(body=draft))
        assert status == 201
        stub.assert_no_pending_responses()
