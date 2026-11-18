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
    condition     = length(aws_apigatewayv2_route.management) > 0 && alltrue([for route in aws_apigatewayv2_route.management : route.authorization_type == "JWT" && route.authorization_scopes == toset(["registry-portal/catalog.read"])])
    error_message = "管理 handler の追加 route も、本体スタックと同じポータル用の scope を JWT authorizer で検証してください。"
  }
}
