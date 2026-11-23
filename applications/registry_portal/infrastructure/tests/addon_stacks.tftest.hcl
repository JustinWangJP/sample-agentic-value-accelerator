# 追加スタック（decision・notification_events）の初回作成を模した plan で、IAM policy が確定することを確かめる。
# 未確定の policy は scripts/check_plan.py が「未確定の IAM policy」で中止するため、ロググループや DLQ の ARN は
# 作成前の資源の属性ではなく、名前と plan 時に確定する呼び出し元のアカウント ID・リージョンから組み立てる。
# Registry や補助テーブルの ARN とは異なるアカウント ID・リージョンを返し、どちらから組み立てたかを区別する
mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111122223333" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-west-2" }
  }
}

run "decision_policy_is_known_on_first_plan" {
  command = plan
  module {
    source = "./modules/decision"
  }
  variables {
    name_prefix           = "registry-portal-dev-decision"
    lambda_zip_path       = "tests/fixtures/safe.json"
    cognito_issuer        = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Example"
    human_client_id       = "human-spa-client"
    registry_arn          = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name = "registry-auxiliary"
    supplement_table_arn  = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
    rest_api = {
      id                   = "a1b2c3d4e5"
      stage_name           = "v1"
      authorizer_id        = "abc123"
      portal_api_scope     = "registry-portal/api.invoke"
      cors_allowed_origins = ["https://portal.example.cloudfront.net"]
    }
    discovery_client = {
      token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      client_id         = "discovery-client"
      client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
      scope             = "registry/discovery.read"
    }
  }
  assert {
    condition = [
      for statement in jsondecode(aws_iam_role_policy.decision.policy).Statement : statement.Resource
      if statement.Sid == "DecisionLogs"
    ] == ["arn:aws:logs:us-west-2:111122223333:log-group:/aws/lambda/registry-portal-dev-decision:*"]
    error_message = "決裁ロールの policy は、ロググループの ARN を名前から組み立てて初回の plan で確定させてください。"
  }
  assert {
    condition     = module.log_group.retention_in_days == 365
    error_message = "log_retention_in_days を指定しない場合、決裁 Lambda のログは今までどおり365日保存してください。"
  }
}

run "events_policy_is_known_on_first_plan" {
  command = plan
  module {
    source = "./modules/notification_events"
  }
  variables {
    name_prefix           = "registry-portal-dev-events"
    lambda_zip_path       = "tests/fixtures/safe.json"
    registry_arn          = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name = "registry-auxiliary"
    supplement_table_arn  = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
  }
  assert {
    condition = [
      for statement in jsondecode(aws_iam_role_policy.events.policy).Statement : statement.Resource
      if contains(["EventsLogs", "EventsDeadLetter"], statement.Sid)
      ] == [
      "arn:aws:logs:us-west-2:111122223333:log-group:/aws/lambda/registry-portal-dev-events:*",
      "arn:aws:sqs:us-west-2:111122223333:registry-portal-dev-events-dlq",
    ]
    error_message = "イベント処理ロールの policy は、ロググループと DLQ の ARN を名前から組み立てて初回の plan で確定させてください。"
  }
  assert {
    condition     = module.log_group.retention_in_days == 365
    error_message = "log_retention_in_days を指定しない場合、イベント処理 Lambda のログは今までどおり365日保存してください。"
  }
}

# ログの保持期間（#62）。追加スタックの Lambda のロググループも、本体と同じ変数で保持日数を変えられる
run "decision_log_retention_dev_value" {
  command = plan
  module {
    source = "./modules/decision"
  }
  variables {
    name_prefix           = "registry-portal-dev-decision"
    lambda_zip_path       = "tests/fixtures/safe.json"
    cognito_issuer        = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Example"
    human_client_id       = "human-spa-client"
    registry_arn          = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name = "registry-auxiliary"
    supplement_table_arn  = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
    rest_api = {
      id                   = "a1b2c3d4e5"
      stage_name           = "v1"
      authorizer_id        = "abc123"
      portal_api_scope     = "registry-portal/api.invoke"
      cors_allowed_origins = ["https://portal.example.cloudfront.net"]
    }
    discovery_client = {
      token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      client_id         = "discovery-client"
      client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
      scope             = "registry/discovery.read"
    }
    log_retention_in_days = 14
  }
  assert {
    condition     = module.log_group.retention_in_days == 14
    error_message = "決裁 Lambda のロググループの保持日数に log_retention_in_days を使ってください。"
  }
}

run "events_log_retention_dev_value" {
  command = plan
  module {
    source = "./modules/notification_events"
  }
  variables {
    name_prefix           = "registry-portal-dev-events"
    lambda_zip_path       = "tests/fixtures/safe.json"
    registry_arn          = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name = "registry-auxiliary"
    supplement_table_arn  = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
    log_retention_in_days = 14
  }
  assert {
    condition     = module.log_group.retention_in_days == 14
    error_message = "イベント処理 Lambda のロググループの保持日数に log_retention_in_days を使ってください。"
  }
}

# 追加スタックのルートモジュールも、CloudWatch Logs が受け付けない保持日数を validation で止める
run "decision_stack_rejects_unsupported_log_retention" {
  command = plan
  module {
    source = "./decision"
  }
  variables {
    region                = "us-east-1"
    lambda_zip_path       = "tests/fixtures/safe.json"
    cognito_user_pool_id  = "us-east-1_Example"
    human_client_id       = "human-spa-client"
    registry_arn          = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name = "registry-auxiliary"
    supplement_table_arn  = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
    rest_api = {
      id                   = "a1b2c3d4e5"
      stage_name           = "v1"
      authorizer_id        = "abc123"
      portal_api_scope     = "registry-portal/api.invoke"
      cors_allowed_origins = ["https://portal.example.cloudfront.net"]
    }
    discovery_token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
    discovery_client_id         = "discovery-client"
    discovery_client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
    discovery_scope             = "registry/discovery.read"
    log_retention_in_days       = 10
  }
  expect_failures = [var.log_retention_in_days]
}

run "events_stack_rejects_unsupported_log_retention" {
  command = plan
  module {
    source = "./notification_events"
  }
  variables {
    region                = "us-east-1"
    lambda_zip_path       = "tests/fixtures/safe.json"
    registry_arn          = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name = "registry-auxiliary"
    supplement_table_arn  = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
    log_retention_in_days = 10
  }
  expect_failures = [var.log_retention_in_days]
}

# 予約同時実行数（#61 実装案 5）。既定値では属性を設定せず（予約なし）、今の動作と差分を出さない
run "decision_reserved_concurrency" {
  command = plan
  module {
    source = "./modules/decision"
  }
  variables {
    name_prefix           = "registry-portal-dev-decision"
    lambda_zip_path       = "tests/fixtures/safe.json"
    cognito_issuer        = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Example"
    human_client_id       = "human-spa-client"
    registry_arn          = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name = "registry-auxiliary"
    supplement_table_arn  = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
    rest_api = {
      id                   = "a1b2c3d4e5"
      stage_name           = "v1"
      authorizer_id        = "abc123"
      portal_api_scope     = "registry-portal/api.invoke"
      cors_allowed_origins = ["https://portal.example.cloudfront.net"]
    }
    discovery_client = {
      token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      client_id         = "discovery-client"
      client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
      scope             = "registry/discovery.read"
    }
    lambda_reserved_concurrency = 5
  }
  assert {
    condition     = aws_lambda_function.decision.reserved_concurrent_executions == 5
    error_message = "lambda_reserved_concurrency を指定したら、決裁 Lambda の reserved_concurrent_executions に渡してください。"
  }
}

run "decision_reserved_concurrency_default" {
  command = plan
  module {
    source = "./modules/decision"
  }
  variables {
    name_prefix           = "registry-portal-dev-decision"
    lambda_zip_path       = "tests/fixtures/safe.json"
    cognito_issuer        = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Example"
    human_client_id       = "human-spa-client"
    registry_arn          = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name = "registry-auxiliary"
    supplement_table_arn  = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
    rest_api = {
      id                   = "a1b2c3d4e5"
      stage_name           = "v1"
      authorizer_id        = "abc123"
      portal_api_scope     = "registry-portal/api.invoke"
      cors_allowed_origins = ["https://portal.example.cloudfront.net"]
    }
    discovery_client = {
      token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      client_id         = "discovery-client"
      client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
      scope             = "registry/discovery.read"
    }
  }
  assert {
    condition     = aws_lambda_function.decision.reserved_concurrent_executions == null
    error_message = "lambda_reserved_concurrency を指定しないときは、決裁 Lambda の同時実行数を予約しないでください。"
  }
}

run "events_reserved_concurrency" {
  command = plan
  module {
    source = "./modules/notification_events"
  }
  variables {
    name_prefix                 = "registry-portal-dev-events"
    lambda_zip_path             = "tests/fixtures/safe.json"
    registry_arn                = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name       = "registry-auxiliary"
    supplement_table_arn        = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
    lambda_reserved_concurrency = 3
  }
  assert {
    condition     = aws_lambda_function.events.reserved_concurrent_executions == 3
    error_message = "lambda_reserved_concurrency を指定したら、イベント処理 Lambda の reserved_concurrent_executions に渡してください。"
  }
}

run "events_reserved_concurrency_default" {
  command = plan
  module {
    source = "./modules/notification_events"
  }
  variables {
    name_prefix           = "registry-portal-dev-events"
    lambda_zip_path       = "tests/fixtures/safe.json"
    registry_arn          = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name = "registry-auxiliary"
    supplement_table_arn  = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
  }
  assert {
    condition     = aws_lambda_function.events.reserved_concurrent_executions == null
    error_message = "lambda_reserved_concurrency を指定しないときは、イベント処理 Lambda の同時実行数を予約しないでください。"
  }
}
