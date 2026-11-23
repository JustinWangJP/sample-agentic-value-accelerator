# REST API のパスは本体スタックが作る。追加スタックは既存パスを参照してメソッドだけを追加する。
locals {
  route_parts = { for name, route in var.routes : name => split(" ", route.route_key) }
  paths       = toset([for parts in values(local.route_parts) : parts[1]])
  resource_ids = var.resource_ids != null ? var.resource_ids : {
    for path, resource in data.aws_api_gateway_resource.portal : path => resource.id
  }
  api_execution_arn = "arn:${data.aws_partition.current.partition}:execute-api:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:${var.rest_api_id}"
  invoke_paths = {
    for name, parts in local.route_parts :
    name => "${parts[0]}${replace(parts[1], "/\\{[^}]+\\}/", "*")}"
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

data "aws_api_gateway_resource" "portal" {
  for_each    = var.resource_ids == null ? local.paths : toset([])
  rest_api_id = var.rest_api_id
  path        = each.key
}

resource "aws_api_gateway_method" "route" {
  for_each             = var.routes
  rest_api_id          = var.rest_api_id
  resource_id          = local.resource_ids[local.route_parts[each.key][1]]
  http_method          = local.route_parts[each.key][0]
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = var.authorizer_id
  authorization_scopes = [var.authorization_scope]
  request_parameters   = { "method.request.header.Origin" = false }
}

resource "aws_api_gateway_integration" "route" {
  for_each                = aws_api_gateway_method.route
  rest_api_id             = var.rest_api_id
  resource_id             = each.value.resource_id
  http_method             = each.value.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.lambda_invoke_arn
}

resource "aws_lambda_permission" "management_api" {
  for_each       = var.routes
  statement_id   = each.value.statement_id
  action         = "lambda:InvokeFunction"
  function_name  = var.management_function_name
  principal      = "apigateway.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
  source_arn     = "${local.api_execution_arn}/*/${local.invoke_paths[each.key]}"
}
