# 追加スタック（別 state）が本体スタックの HTTP API に割り当てる route も、本体と同じ
# カスタム scope を JWT authorizer で検証することを確かめる。
mock_provider "aws" {
  mock_data "aws_apigatewayv2_api" {
    defaults = { execution_arn = "arn:aws:execute-api:us-east-1:123456789012:abcde12345" }
  }
}

run "decision_routes_require_portal_scope" {
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
    supplement_table_name       = "registry-portal-dev-auxiliary"
    supplement_table_arn        = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-portal-dev-auxiliary"
    http_api_id                 = "abcde12345"
    jwt_authorizer_id           = "abc123"
    discovery_token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
    discovery_client_id         = "discovery-client"
    discovery_client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
    discovery_scope             = ""
  }
  assert {
    condition     = length(aws_apigatewayv2_route.decision) > 0 && alltrue([for route in aws_apigatewayv2_route.decision : route.authorization_type == "JWT" && route.authorization_scopes == toset(["registry-portal/catalog.read"])])
    error_message = "決裁の route も、本体スタックと同じポータル用の scope を JWT authorizer で検証してください。"
  }
}

run "management_routes_require_portal_scope" {
  command = plan
  module {
    source = "./modules/management_routes"
  }
  variables {
    http_api_id               = "abcde12345"
    jwt_authorizer_id         = "abc123"
    management_integration_id = "int1234"
    management_function_name  = "registry-portal-dev-management"
  }
  assert {
    condition     = length(aws_apigatewayv2_route.management) > 0 && alltrue([for route in aws_apigatewayv2_route.management : route.authorization_type == "JWT" && route.authorization_scopes == toset(["registry-portal/catalog.read"]) && route.authorizer_id == var.jwt_authorizer_id && route.target == "integrations/${var.management_integration_id}"])
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
    ]), toset([for route in aws_apigatewayv2_route.management : route.route_key]))) == 0
    error_message = "下書きの route が不足しています。${join(", ", sort(tolist(setsubtract(toset([
      "POST /api/records",
      "GET /api/records",
      "GET /api/records/{recordId}",
      "PATCH /api/records/{recordId}",
      "GET /api/operations/{operationId}",
      "POST /api/records/{recordId}/submit",
    ]), toset([for route in aws_apigatewayv2_route.management : route.route_key])))))}"
  }
  assert {
    # 本体スタックの aws_lambda_permission.api と同じく、stage 以外はメソッドと path を route ごとに固定する
    condition = toset([for permission in aws_lambda_permission.management_api :
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
    ]) && length(aws_lambda_permission.management_api) == length(aws_apigatewayv2_route.management)
    error_message = "管理 Lambda の呼び出し許可は、route ごとのメソッドと path に限ってください（メソッドを * にしない）。"
  }
  assert {
    condition     = alltrue([for permission in aws_lambda_permission.management_api : permission.function_name == var.management_function_name && permission.principal == "apigateway.amazonaws.com" && permission.action == "lambda:InvokeFunction"])
    error_message = "下書きの各 route に、既存の管理 Lambda を呼び出す許可が必要です。"
  }
}
