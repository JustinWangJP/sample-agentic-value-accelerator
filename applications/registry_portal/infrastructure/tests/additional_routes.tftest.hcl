# 追加スタック（別 state）が本体スタックの HTTP API に割り当てる route も、本体と同じ
# カスタム scope を JWT authorizer で検証することを確かめる。
mock_provider "aws" {
  mock_data "aws_apigatewayv2_api" {
    defaults = { execution_arn = "arn:aws:execute-api:us-east-1:123456789012:abcde12345" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
}

run "decision_routes_require_portal_scope" {
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
    supplement_table_name = "registry-portal-dev-auxiliary"
    supplement_table_arn  = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-portal-dev-auxiliary"
    http_api_id           = "abcde12345"
    jwt_authorizer_id     = "abc123"
    portal_api_scope      = "registry-portal/api.invoke"
    discovery_client = {
      token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      client_id         = "discovery-client"
      client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
      scope             = ""
    }
  }
  assert {
    condition     = length(aws_apigatewayv2_route.decision) > 0 && alltrue([for route in aws_apigatewayv2_route.decision : route.authorization_type == "JWT" && route.authorization_scopes == toset(["registry-portal/api.invoke"])])
    error_message = "決裁の route も、本体スタックと同じポータル用の scope を JWT authorizer で検証してください。"
  }
}

run "management_routes_require_portal_scope" {
  command = plan
  # route 表（#51 の下書きと操作照会の 6 経路を含む）は management スタックのルートモジュールが持つ
  module {
    source = "./management"
  }
  variables {
    region                    = "us-east-1"
    management_role_name      = "registry-portal-dev-management"
    registry_arn              = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_arn      = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-portal-dev-auxiliary"
    http_api_id               = "abcde12345"
    jwt_authorizer_id         = "abc123"
    management_integration_id = "int1234"
    management_function_name  = "registry-portal-dev-management"
    portal_api_scope          = "registry-portal/api.invoke"
  }
  assert {
    condition     = length(module.management_routes.routes) > 0 && alltrue([for route in values(module.management_routes.routes) : route.authorization_type == "JWT" && route.authorization_scopes == toset(["registry-portal/api.invoke"]) && route.authorizer_id == var.jwt_authorizer_id && route.target == "integrations/${var.management_integration_id}"])
    error_message = "管理 handler の追加 route も、本体スタックと同じポータル用の scope を JWT authorizer で検証してください。"
  }
  assert {
    condition = length(setsubtract(toset([
      "POST /api/records",
      "GET /api/records",
      "GET /api/records/{recordId}",
      "PATCH /api/records/{recordId}",
      "GET /api/operations/{operationId}",
      "POST /api/records/{recordId}/submit",
    ]), toset([for route in values(module.management_routes.routes) : route.route_key]))) == 0
    error_message = "下書きの route が不足しています。${join(", ", sort(tolist(setsubtract(toset([
      "POST /api/records",
      "GET /api/records",
      "GET /api/records/{recordId}",
      "PATCH /api/records/{recordId}",
      "GET /api/operations/{operationId}",
      "POST /api/records/{recordId}/submit",
    ]), toset([for route in values(module.management_routes.routes) : route.route_key])))))}"
  }
  assert {
    # 本体スタックの route と同じく、stage 以外はメソッドと path を route ごとに固定する
    condition = toset([for permission in values(module.management_routes.invoke_permissions) :
      trimprefix(permission.source_arn, "arn:aws:execute-api:us-east-1:123456789012:abcde12345/*/")
      ]) == toset([
      "GET/api/admin/records",
      "GET/api/admin/connection",
      "GET/api/records/*/history",
      "POST/api/records/*/deprecation-requests",
      "GET/api/notifications",
      "POST/api/notifications/read",
      "POST/api/records",
      "GET/api/records",
      "GET/api/records/*",
      "PATCH/api/records/*",
      "GET/api/operations/*",
      "POST/api/records/*/submit",
    ]) && keys(module.management_routes.invoke_permissions) == keys(module.management_routes.routes)
    error_message = "管理 Lambda の呼び出し許可は、route ごとのメソッドと path に限ってください（メソッドを * にしない）。"
  }
  assert {
    condition     = alltrue([for permission in values(module.management_routes.invoke_permissions) : permission.function_name == var.management_function_name && permission.principal == "apigateway.amazonaws.com" && permission.source_account == "123456789012"])
    error_message = "下書きの各 route に、同じアカウントの API Gateway から既存の管理 Lambda を呼び出す許可が必要です。"
  }
}
