import json
from pathlib import Path


MODULE = (
    Path(__file__).resolve().parents[2] / "infrastructure/modules/management_drafts/main.tf.json"
)


def test_draft_policy_only_grants_record_writes_and_supplement_transactions():
    config = json.loads(MODULE.read_text())
    policy = config["locals"]["management_draft_policy"]
    statements = policy["Statement"]
    assert statements == [
        {
            "Sid": "RegistryDrafts",
            "Effect": "Allow",
            "Action": [
                "agent-registry:CreateRegistryRecord",
                "agent-registry:UpdateRegistryRecord",
                "agent-registry:GetRegistryRecord",
                "agent-registry:ListRegistryRecords",
            ],
            "Resource": ["${var.registry_arn}", "${var.registry_arn}/record/*"],
        },
        {
            "Sid": "DraftSupplement",
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"],
            "Resource": "${var.supplement_table_arn}",
        },
    ]
    resources = config["resource"]
    assert set(resources) == {"aws_iam_role_policy"}
    role_policy = resources["aws_iam_role_policy"]["management_drafts"]
    assert role_policy["role"] == "${var.management_role_name}"
    assert role_policy["policy"] == "${jsonencode(local.management_draft_policy)}"
