mock_provider "aws" {
  # provider のリージョン（us-east-1 に固定）
  mock_data "aws_region" {
    defaults = { region = "us-east-1" }
  }
  # 実際の S3 バケットと同じく ID をバケット名にする（plan の時点で付属設定の対象バケットを確かめるため）
  mock_resource "aws_s3_bucket" {
    override_during = plan
    defaults        = { id = "portal-state-test-123456789012" }
  }
  mock_resource "aws_ssoadmin_permission_set" {
    defaults = { arn = "arn:aws:sso:::permissionSet/ssoins-0000000000000000/ps-0000000000000000" }
  }
}
# 既定は IAM Identity Center のない個人アカウント。state の主体は IAM ロールの RegistryPortalDeployer。
variables {
  aws_account_id           = "123456789012"
  bootstrap_admin_role_arn = "arn:aws:iam::123456789012:role/BootstrapAdministrator"
  deployer_principal_arn   = "arn:aws:iam::123456789012:role/RegistryPortalDeployer"
  state_bucket_name        = "portal-state-test-123456789012"
  spa_bucket_name          = "portal-spa-test-123456789012"
  cognito_user_pool_id     = "us-east-1_Example"
}
run "personal_account_without_identity_center" {
  command = plan
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled" && aws_s3_bucket_server_side_encryption_configuration.state.rule != null && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "state はバージョニング、暗号化、公開禁止が必要です。"
  }
  assert {
    condition     = length(aws_ssoadmin_permission_set.deployer) == 0 && length(aws_ssoadmin_permission_set_inline_policy.deployer) == 0 && length(aws_ssoadmin_account_assignment.deployer) == 0
    error_message = "Identity Center を指定しない場合は permission set、inline policy、割り当てを作らないでください。"
  }
  assert {
    condition = anytrue([
      for statement in jsondecode(aws_s3_bucket_policy.state.policy).Statement :
      toset(statement.Action) == toset(["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*"]) &&
      statement.Effect == "Deny" &&
      statement.Resource == "arn:aws:s3:::portal-state-test-123456789012/*" &&
      statement.Condition.ArnNotLike["aws:PrincipalArn"] == "arn:aws:iam::123456789012:role/RegistryPortalDeployer"
      if statement.Sid == "StateOnlyForDeployer"
    ])
    error_message = "state の内容は管理者を例外にせず、指定した deployer の主体だけに限定してください。"
  }
  assert {
    condition = anytrue([
      for statement in jsondecode(aws_s3_bucket_policy.state.policy).Statement :
      statement.Sid == "StateListOnlyForOperators" && try(
        statement.Effect == "Deny" &&
        toset(statement.Action) == toset(["s3:ListBucket*"]) &&
        statement.Resource == "arn:aws:s3:::portal-state-test-123456789012" &&
        toset(statement.Condition.ArnNotLike["aws:PrincipalArn"]) == toset([
          "arn:aws:iam::123456789012:role/RegistryPortalDeployer",
          "arn:aws:iam::123456789012:role/BootstrapAdministrator"
        ]), false
      )
    ])
    error_message = "HeadBucket 用の一覧権限は指定した bootstrap 管理ロールと deployer だけを拒否の例外にしてください。"
  }
  assert {
    condition     = !strcontains(aws_s3_bucket_policy.state.policy, "AWSReservedSSO")
    error_message = "個人アカウントの state バケットポリシーに SSO ロールを固定しないでください。"
  }
}
# state の古いバージョンは期限で消す。古い state に戻せるよう、日数は長めにし、直近のバージョンは常に残す。
# prevent_destroy は tftest では検査できないため、infrastructure/tests/test_teardown_guards.py で確認する。
run "state_bucket_expires_old_versions" {
  command = plan
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "ライフサイクル設定を追加しても、state のバージョニングは有効のまま残してください。"
  }
  assert {
    condition     = aws_s3_bucket_lifecycle_configuration.state.bucket == "portal-state-test-123456789012"
    error_message = "ライフサイクル設定は state バケットに付けてください。"
  }
  assert {
    condition = anytrue([
      for rule in aws_s3_bucket_lifecycle_configuration.state.rule :
      rule.status == "Enabled" &&
      try(rule.noncurrent_version_expiration[0].noncurrent_days, 0) >= 90 &&
      try(rule.noncurrent_version_expiration[0].newer_noncurrent_versions, 0) >= 1
    ])
    error_message = "古いバージョンは 90 日以上残してから消し、直近のバージョンは常に残してください。"
  }
  assert {
    condition = anytrue([
      for rule in aws_s3_bucket_lifecycle_configuration.state.rule :
      rule.status == "Enabled" &&
      try(rule.abort_incomplete_multipart_upload[0].days_after_initiation, 0) == 1
    ])
    error_message = "未完了のマルチパートアップロードは 1 日で中止してください。"
  }
  assert {
    condition = alltrue([
      for rule in aws_s3_bucket_lifecycle_configuration.state.rule :
      length(rule.expiration) == 0 && length(rule.transition) == 0
    ])
    error_message = "現行の state を失効・移行させないでください。消すのは古いバージョンだけです。"
  }
}
run "identity_center_permission_set" {
  command = plan
  variables {
    deployer_principal_arn       = "arn:aws:iam::123456789012:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_RegistryPortalDeployer_*"
    identity_center_instance_arn = "arn:aws:sso:::instance/ssoins-0000000000000000"
    deployer_group_id            = "00000000-0000-0000-0000-000000000000"
  }
  assert {
    condition     = length(aws_ssoadmin_permission_set.deployer) == 1 && length(aws_ssoadmin_account_assignment.deployer) == 1
    error_message = "Identity Center を指定した場合は permission set と割り当てを作ってください。"
  }
  assert {
    condition     = aws_ssoadmin_permission_set.deployer[0].name == "RegistryPortalDeployer" && aws_ssoadmin_permission_set.deployer[0].session_duration == "PT1H"
    error_message = "反映には専用の短期 permission set を使用してください。"
  }
  assert {
    condition = anytrue([
      for statement in jsondecode(aws_s3_bucket_policy.state.policy).Statement :
      statement.Condition.ArnNotLike["aws:PrincipalArn"] == "arn:aws:iam::123456789012:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_RegistryPortalDeployer_*"
      if statement.Sid == "StateOnlyForDeployer"
    ])
    error_message = "state アクセスは指定した SSO ロールに限定してください。"
  }
  assert {
    condition     = jsondecode(aws_ssoadmin_permission_set_inline_policy.deployer[0].inline_policy).Statement[0].Effect == "Deny" && contains(jsondecode(aws_ssoadmin_permission_set_inline_policy.deployer[0].inline_policy).Statement[0].Action, "agent-registry:UpdateRegistryRecordStatus") && contains(jsondecode(aws_ssoadmin_permission_set_inline_policy.deployer[0].inline_policy).Statement[0].Action, "agent-registry:DeleteRegistry")
    error_message = "deployer に Registry の書き込みと決裁を拒否してください。"
  }
  assert {
    condition = alltrue([
      for action in ["agent-registry:SubmitRegistryRecordForApproval", "bedrock-agentcore:SubmitRegistryRecordForApproval"] :
      contains(jsondecode(aws_ssoadmin_permission_set_inline_policy.deployer[0].inline_policy).Statement[0].Action, action)
    ])
    error_message = "deployer には新旧 Registry API の申請操作を明示的に拒否してください。"
  }
  assert {
    condition     = length(replace(aws_ssoadmin_permission_set_inline_policy.deployer[0].inline_policy, "/\\s/", "")) <= 10240
    error_message = "permission set の inline policy は非空白10240文字以内にしてください。"
  }
}
run "identity_center_requires_group" {
  command = plan
  variables {
    identity_center_instance_arn = "arn:aws:sso:::instance/ssoins-0000000000000000"
  }
  expect_failures = [var.deployer_group_id]
}
run "deployer_principal_must_be_same_account_role" {
  command = plan
  variables {
    deployer_principal_arn = "arn:aws:iam::999999999999:role/RegistryPortalDeployer"
  }
  expect_failures = [var.deployer_principal_arn]
}
run "deployer_principal_must_not_be_session" {
  command = plan
  variables {
    deployer_principal_arn = "arn:aws:sts::123456789012:assumed-role/RegistryPortalDeployer/operator"
  }
  expect_failures = [var.deployer_principal_arn]
}
# 月額の予算アラート。上限と通知先の両方を指定したときだけ作る（通知先は TF_VAR_budget_notification_emails で渡す）
run "budget_with_limit_and_emails" {
  command = plan
  variables {
    budget_limit_usd           = 5
    budget_notification_emails = ["portal-owner@example.com"]
  }
  assert {
    condition     = length(aws_budgets_budget.monthly) == 1
    error_message = "上限と通知先の両方を指定した場合は予算を 1 つ作ってください。"
  }
  assert {
    condition = (
      aws_budgets_budget.monthly[0].budget_type == "COST" &&
      aws_budgets_budget.monthly[0].time_unit == "MONTHLY" &&
      tonumber(aws_budgets_budget.monthly[0].limit_amount) == 5 &&
      aws_budgets_budget.monthly[0].limit_unit == "USD"
    )
    error_message = "予算は月額の COST 予算とし、上限を USD で指定してください。"
  }
  assert {
    condition = toset([
      for n in aws_budgets_budget.monthly[0].notification :
      "${n.notification_type}:${n.comparison_operator}:${n.threshold}:${n.threshold_type}"
    ]) == toset(["ACTUAL:GREATER_THAN:80:PERCENTAGE", "FORECASTED:GREATER_THAN:100:PERCENTAGE"])
    error_message = "通知は実績 80% と予測 100% の 2 つにしてください。"
  }
  assert {
    condition = alltrue([
      for n in aws_budgets_budget.monthly[0].notification :
      toset(n.subscriber_email_addresses) == toset(["portal-owner@example.com"])
    ])
    error_message = "どちらの通知も指定したメールアドレスに送ってください。"
  }
}
run "budget_not_created_by_default" {
  command = plan
  assert {
    condition     = length(aws_budgets_budget.monthly) == 0
    error_message = "既定値では予算を作らないでください。"
  }
}
run "budget_not_created_without_emails" {
  command = plan
  variables {
    budget_limit_usd = 5
  }
  assert {
    condition     = length(aws_budgets_budget.monthly) == 0
    error_message = "通知先を指定しない場合は予算を作らないでください。"
  }
}
run "budget_not_created_without_limit" {
  command = plan
  variables {
    budget_notification_emails = ["portal-owner@example.com"]
  }
  assert {
    condition     = length(aws_budgets_budget.monthly) == 0
    error_message = "上限を指定しない場合は予算を作らないでください。"
  }
}
