mock_provider "aws" {
  mock_resource "aws_ssoadmin_permission_set" {
    defaults = { arn = "arn:aws:sso:::permissionSet/ssoins-0000000000000000/ps-0000000000000000" }
  }
}
variables {
  aws_account_id               = "123456789012"
  bootstrap_admin_role_arn     = "arn:aws:iam::123456789012:role/BootstrapAdministrator"
  state_bucket_name            = "portal-state-test-123456789012"
  spa_bucket_name              = "portal-spa-test-123456789012"
  cognito_user_pool_id         = "us-east-1_Example"
  identity_center_instance_arn = "arn:aws:sso:::instance/ssoins-0000000000000000"
  deployer_group_id            = "00000000-0000-0000-0000-000000000000"
}
run "state_and_deployer" {
  command = plan
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled" && aws_s3_bucket_server_side_encryption_configuration.state.rule != null && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "state はバージョニング、暗号化、公開禁止が必要です。"
  }
  assert {
    condition     = aws_ssoadmin_permission_set.deployer.name == "RegistryPortalDeployer" && aws_ssoadmin_permission_set.deployer.session_duration == "PT1H"
    error_message = "反映には専用の短期 permission set を使用してください。"
  }
  assert {
    condition     = strcontains(aws_s3_bucket_policy.state.policy, "StateOnlyForDeployer") && strcontains(aws_s3_bucket_policy.state.policy, "AWSReservedSSO_RegistryPortalDeployer_")
    error_message = "state アクセスは専用 SSO ロールに限定してください。"
  }
  assert {
    condition = alltrue([
      for statement in jsondecode(aws_s3_bucket_policy.state.policy).Statement :
      toset(statement.Action) == toset(["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*"]) &&
      statement.Effect == "Deny" &&
      statement.Resource == "arn:aws:s3:::portal-state-test-123456789012/*" &&
      statement.Condition.ArnNotLike["aws:PrincipalArn"] == "arn:aws:iam::123456789012:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_RegistryPortalDeployer_*"
      if statement.Sid == "StateOnlyForDeployer"
    ])
    error_message = "state の内容は管理者を例外にせず deployer だけに限定してください。"
  }
  assert {
    condition = anytrue([
      for statement in jsondecode(aws_s3_bucket_policy.state.policy).Statement :
      statement.Sid == "StateListOnlyForOperators" && try(
        statement.Effect == "Deny" &&
        toset(statement.Action) == toset(["s3:ListBucket*"]) &&
        statement.Resource == "arn:aws:s3:::portal-state-test-123456789012" &&
        toset(statement.Condition.ArnNotLike["aws:PrincipalArn"]) == toset([
          "arn:aws:iam::123456789012:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_RegistryPortalDeployer_*",
          "arn:aws:iam::123456789012:role/BootstrapAdministrator"
        ]), false
      )
    ])
    error_message = "HeadBucket 用の一覧権限は指定した bootstrap 管理ロールと deployer だけを拒否の例外にしてください。"
  }
  assert {
    condition     = jsondecode(aws_ssoadmin_permission_set_inline_policy.deployer.inline_policy).Statement[0].Effect == "Deny" && contains(jsondecode(aws_ssoadmin_permission_set_inline_policy.deployer.inline_policy).Statement[0].Action, "agent-registry:UpdateRegistryRecordStatus") && contains(jsondecode(aws_ssoadmin_permission_set_inline_policy.deployer.inline_policy).Statement[0].Action, "agent-registry:DeleteRegistry")
    error_message = "deployer に Registry の書き込みと決裁を拒否してください。"
  }
  assert {
    condition = alltrue([
      for action in ["agent-registry:SubmitRegistryRecordForApproval", "bedrock-agentcore:SubmitRegistryRecordForApproval"] :
      contains(jsondecode(aws_ssoadmin_permission_set_inline_policy.deployer.inline_policy).Statement[0].Action, action)
    ])
    error_message = "deployer には新旧 Registry API の申請操作を明示的に拒否してください。"
  }
  assert {
    condition     = length(replace(aws_ssoadmin_permission_set_inline_policy.deployer.inline_policy, "/\\s/", "")) <= 10240
    error_message = "permission set の inline policy は非空白10240文字以内にしてください。"
  }
}
