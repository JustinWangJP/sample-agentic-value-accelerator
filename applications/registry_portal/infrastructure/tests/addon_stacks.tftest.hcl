# 追加スタック（decision・notification_events）の初回作成を模した plan で、IAM policy が確定することを確かめる。
# 未確定の policy は scripts/check_plan.py が「未確定の IAM policy」で中止するため、ロググループや DLQ の ARN は
# 作成前の資源の属性ではなく、名前と plan 時に確定する呼び出し元のアカウント ID・リージョンから組み立てる。
# Registry や補助テーブルの ARN とは異なるアカウント ID・リージョンを返し、どちらから組み立てたかを区別する
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111122223333" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-west-2" }
  }
  # 借用する HTTP API。Lambda の呼び出し許可の source_arn に使う
  mock_data "aws_apigatewayv2_api" {
    defaults = { execution_arn = "arn:aws:execute-api:us-east-1:123456789012:a1b2c3d4e5" }
  }
}

run "decision_policy_is_known_on_first_plan" {
  command = plan
  module {
    source = "./modules/decision"
  }
  variables {
    name_prefix                 = "registry-portal-dev-decision"
    lambda_zip_path             = "tests/fixtures/safe.json"
    cognito_issuer              = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Example"
    human_client_id             = "human-spa-client"
    registry_arn                = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_name       = "registry-auxiliary"
    supplement_table_arn        = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-auxiliary"
    http_api_id                 = "a1b2c3d4e5"
    jwt_authorizer_id           = "abc123"
    discovery_token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
    discovery_client_id         = "discovery-client"
    discovery_client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
    discovery_scope             = "registry/discovery.read"
  }
  assert {
    condition = [
      for statement in jsondecode(aws_iam_role_policy.decision.policy).Statement : statement.Resource
      if statement.Sid == "DecisionLogs"
    ] == ["arn:aws:logs:us-west-2:111122223333:log-group:/aws/lambda/registry-portal-dev-decision:*"]
    error_message = "決裁ロールの policy は、ロググループの ARN を名前から組み立てて初回の plan で確定させてください。"
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
}
