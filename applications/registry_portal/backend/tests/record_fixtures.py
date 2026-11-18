"""A2A・MCP サーバー・Skill の下書きの Seam 1 テストで共有する fixture と Stubber の応答。"""

import json

from botocore.stub import ANY

from catalog_helpers import REGISTRY_ARN
from draft_helpers import RECORD_ARN, RECORD_ID, aws_record


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


COMMON = {
    "displayName": "決済",
    "description": "決済を調べる",
    "recordVersion": "1.0.0",
    "ownerTeam": "team-payments",
    "environment": "dev",
    "dataClassification": "private",
    "connectionReference": "https://example.com/payments",
    "contact": "payments@example.com",
    "termsOfUse": "社内利用のみ",
}
CARD = {
    "name": "payments-agent",
    "description": "決済を調べる",
    "version": "1.0.0",
    "protocolVersion": "0.3.0",
    "url": "https://example.com/a2a",
    "capabilities": {},
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "skills": [{"id": "lookup", "name": "照会", "description": "決済を照会する", "tags": ["pay"]}],
}
SERVER = {"name": "io.example/payments", "description": "決済 MCP サーバー", "version": "1.0.0"}
TOOLS = {
    "tools": [
        {
            "name": "lookup",
            "description": "決済を照会する",
            "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
        }
    ]
}
SKILL_MD = "---\nname: payments-skill\ndescription: 決済を調べる手順\n---\n\n# 手順\n照会する。\n"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + "ab" * 32

A2A_DRAFT = {
    **COMMON,
    "name": "payments-a2a",
    "recordType": "AGENT",
    "descriptorType": "a2aAgentCard",
    "schemaVersion": "0.3",
    "definition": compact(CARD),
}
MCP_DRAFT = {
    **COMMON,
    "name": "payments-mcp",
    "recordType": "MCP",
    "descriptorType": "mcpServer",
    "schemaVersion": "2025-12-11",
    "definition": compact(SERVER),
    "toolsSchemaVersion": "2025-11-25",
    "toolsDefinition": compact(TOOLS),
}
SKILL_DRAFT = {
    **COMMON,
    "name": "payments-skill",
    "recordType": "SKILL",
    "descriptorType": "agentSkillsDefinition",
    "schemaVersion": "0.1.0",
    "skillRepositoryUrl": "https://github.com/example/payments-skill",
    "skillRepositorySource": "github",
    "skillCommit": COMMIT,
    "skillVersion": "1.2.0",
    "skillDigest": DIGEST,
    "skillMd": SKILL_MD,
}

CUSTOM_DEFINITION = {
    "purpose": "照会",
    "connectionReference": COMMON["connectionReference"],
    "authentication": "Cognito JWT",
    "contact": COMMON["contact"],
    "termsOfUse": COMMON["termsOfUse"],
}
CUSTOM_DRAFT = {
    **COMMON,
    "name": "payments-a2a",
    "recordType": "AGENT",
    "descriptorType": "custom",
    "purpose": "照会",
    "authentication": "Cognito JWT",
}
CUSTOM_DESCRIPTORS = {
    "custom": {
        "data": json.dumps(
            CUSTOM_DEFINITION, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    }
}

A2A_DESCRIPTORS = {"a2aAgentCard": {"data": compact(CARD), "dataSchemaVersion": "0.3"}}
MCP_DESCRIPTORS = {
    "mcpServer": {
        "data": compact(SERVER),
        "dataSchemaVersion": "2025-12-11",
        "additionalData": {"tools": {"data": compact(TOOLS), "dataSchemaVersion": "2025-11-25"}},
    }
}
SKILL_DESCRIPTORS = {
    "agentSkillsDefinition": {
        "data": compact(
            {
                "repository": {
                    "url": "https://github.com/example/payments-skill",
                    "source": "github",
                }
            }
        ),
        "dataSchemaVersion": "0.1.0",
        "additionalData": {"skillMd": {"data": SKILL_MD}},
    }
}
METADATA = {"ownerTeam": "team-payments", "environment": "dev", "dataClassification": "private"}

CASES = {
    "a2a": (A2A_DRAFT, A2A_DESCRIPTORS, {**METADATA, "isA2A": True}),
    "mcp": (MCP_DRAFT, MCP_DESCRIPTORS, METADATA),
    "skill": (SKILL_DRAFT, SKILL_DESCRIPTORS, METADATA),
}


def stub_create(stub, draft, descriptors, metadata, returned=None):
    stub.add_response(
        "create_registry_record",
        {"recordArn": RECORD_ARN, "status": "CREATING"},
        {
            "registryId": REGISTRY_ARN,
            "name": draft["name"],
            "displayName": draft["displayName"],
            "description": draft["description"],
            "recordType": draft["recordType"],
            "recordVersion": draft["recordVersion"],
            "descriptors": descriptors,
            "customMetadata": metadata,
            "clientToken": ANY,
        },
    )
    stub.add_response(
        "get_registry_record",
        returned or aws_record(draft, descriptors=descriptors, customMetadata=metadata),
        {"registryId": REGISTRY_ARN, "recordId": RECORD_ID},
    )
