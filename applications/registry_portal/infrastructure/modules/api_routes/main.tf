# 経路表（routes.json）の 1 グループを、REST API のメソッドとして 1 つの Lambda に割り当てる。
# 本体スタック（main）、management スタック（management）、decision スタック（decision）が共有する。
# REST API のパスは本体スタックが作る。追加スタックは既存パスを参照してメソッドだけを追加する。
locals {
  routes = jsondecode(file("${path.module}/routes.json"))[var.route_group]
  paths  = toset([for route in values(local.routes) : route.path])
  resource_ids = var.resource_ids != null ? var.resource_ids : {
    for path, resource in data.aws_api_gateway_resource.portal : path => resource.id
  }
  # 追加スタックは REST API の実行 ARN を受け取らないため、ID から組み立てる
  api_execution_arn = "arn:${data.aws_partition.current.partition}:execute-api:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:${var.rest_api.id}"
  invoke_paths = {
    for name, route in local.routes :
    name => "${route.method}${replace(route.path, "/\\{[^}]+\\}/", "*")}"
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

data "aws_api_gateway_resource" "portal" {
  for_each    = var.resource_ids == null ? local.paths : toset([])
  rest_api_id = var.rest_api.id
  path        = each.key
}

resource "aws_api_gateway_method" "route" {
  for_each             = local.routes
  rest_api_id          = var.rest_api.id
  resource_id          = local.resource_ids[each.value.path]
  http_method          = each.value.method
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = var.rest_api.authorizer_id
  authorization_scopes = [var.rest_api.portal_api_scope]
  request_parameters   = { "method.request.header.Origin" = false }
}

resource "aws_api_gateway_integration" "route" {
  for_each                = aws_api_gateway_method.route
  rest_api_id             = var.rest_api.id
  resource_id             = each.value.resource_id
  http_method             = each.value.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.lambda_invoke_arn
}

# 呼び出し許可は経路ごとに、stage だけを * にしてメソッドとパスを固定する。パスパラメーターは * になり、
# source_arn の * は / もまたぐため、管理 Lambda の GET/api/records/* は決裁 Lambda の GET の経路にも一致する。
# API Gateway は統合先の Lambda だけを呼び出すので実害はない。一致する経路の集合は
# tests/test_route_permissions.py で固定している（README「dev に手動配信する」）。
resource "aws_lambda_permission" "route" {
  for_each       = local.routes
  statement_id   = each.value.statement_id
  action         = "lambda:InvokeFunction"
  function_name  = var.function_name
  principal      = "apigateway.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
  source_arn     = "${local.api_execution_arn}/*/${local.invoke_paths[each.key]}"
}

moved {
  from = aws_lambda_permission.management_api
  to   = aws_lambda_permission.route
}
