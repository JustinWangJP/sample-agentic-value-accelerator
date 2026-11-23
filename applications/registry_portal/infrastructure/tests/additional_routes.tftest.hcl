# 追加スタック（別 state）が本体スタックの REST API に割り当てる route も、本体と同じ
# カスタム scope を Cognito User Pools authorizer で検証することを確かめる。
mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-1" }
  }
  mock_data "aws_lambda_function" {
    defaults = {
      invoke_arn = "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-1:123456789012:function:registry-portal-dev-management/invocations"
    }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
}

# 追加スタックが本体の output rest_api から転記する値。各 run で共通に使う
variables {
  rest_api = {
    id                   = "abcde12345"
    stage_name           = "v1"
    authorizer_id        = "abc123"
    portal_api_scope     = "registry-portal/api.invoke"
    cors_allowed_origins = ["https://portal.example.cloudfront.net"]
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
    discovery_client = {
      token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
      client_id         = "discovery-client"
      client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
      scope             = ""
    }
  }
  assert {
    condition     = length(module.decision_routes.routes) == length(jsondecode(file("modules/api_routes/routes.json")).decision) && alltrue([for route in values(module.decision_routes.routes) : route.authorization_type == "COGNITO_USER_POOLS" && route.authorization_scopes == toset(["registry-portal/api.invoke"])])
    error_message = "決裁の route も、本体スタックと同じポータル用の scope を Cognito User Pools authorizer で検証してください。"
  }
}

run "management_routes_require_portal_scope" {
  command = plan
  # route 表（#51 の下書きと操作照会の 6 経路を含む）は本体と共有する
  module {
    source = "./management"
  }
  variables {
    region                   = "us-east-1"
    management_role_name     = "registry-portal-dev-management"
    registry_arn             = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
    supplement_table_arn     = "arn:aws:dynamodb:us-east-1:123456789012:table/registry-portal-dev-auxiliary"
    management_function_name = "registry-portal-dev-management"
  }
  assert {
    condition     = length(module.management_routes.routes) == length(jsondecode(file("modules/api_routes/routes.json")).management) && alltrue([for route in values(module.management_routes.routes) : route.authorization_type == "COGNITO_USER_POOLS" && route.authorization_scopes == toset(["registry-portal/api.invoke"]) && route.authorizer_id == var.rest_api.authorizer_id && endswith(route.target, ":function:registry-portal-dev-management/invocations")])
    error_message = "管理 handler の追加 route も、本体スタックと同じポータル用の scope を Cognito User Pools authorizer で検証してください。"
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
