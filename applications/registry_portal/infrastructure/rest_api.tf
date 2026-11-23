locals {
  api_stage            = "v1"
  cors_allowed_origins = distinct(concat([local.spa_origin], var.local_dev_origins))
  # 追加スタックの経路を含む、REST API のすべてのパス。経路の割り当ては modules/api_routes が同じ表から読む
  route_paths = toset(flatten([
    for routes in values(jsondecode(file("${path.module}/modules/api_routes/routes.json"))) :
    [for route in values(routes) : route.path]
  ]))
  path_segments = [for path in local.route_paths : split("/", trimprefix(path, "/"))]
  api_resources = merge(
    { "/api" = aws_api_gateway_resource.api.id },
    { for path, resource in aws_api_gateway_resource.depth2 : path => resource.id },
    { for path, resource in aws_api_gateway_resource.depth3 : path => resource.id },
    { for path, resource in aws_api_gateway_resource.depth4 : path => resource.id },
    { for path, resource in aws_api_gateway_resource.depth5 : path => resource.id },
  )
}

resource "aws_api_gateway_rest_api" "portal" {
  name = var.name_prefix
  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_rest_api_policy" "portal" {
  rest_api_id = aws_api_gateway_rest_api.portal.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow", Principal = "*", Action = "execute-api:Invoke", Resource = "execute-api:/*"
      },
      {
        Effect    = "Deny", Principal = "*", Action = "execute-api:Invoke", Resource = "execute-api:/*"
        Condition = { NotIpAddress = { "aws:SourceIp" = var.allowed_source_cidrs } }
      }
    ]
  })
}

resource "aws_api_gateway_authorizer" "human" {
  rest_api_id     = aws_api_gateway_rest_api.portal.id
  name            = "human-access-token"
  type            = "COGNITO_USER_POOLS"
  identity_source = "method.request.header.Authorization"
  provider_arns   = ["arn:${local.partition}:cognito-idp:${local.region}:${var.aws_account_id}:userpool/${var.cognito_user_pool_id}"]
}

resource "aws_api_gateway_resource" "api" {
  rest_api_id = aws_api_gateway_rest_api.portal.id
  parent_id   = aws_api_gateway_rest_api.portal.root_resource_id
  path_part   = "api"
}

# 中間パスを含め、親が先に作られるよう、パスのセグメント数（深さ）ごとに resource を作る。
# 例: /api は深さ 1、/api/catalog は 2、/api/catalog/search は 3。
# resource は自分自身を親として参照できないため、深さごとに別のブロックにする。
resource "aws_api_gateway_resource" "depth2" {
  for_each    = toset([for parts in local.path_segments : "/${join("/", slice(parts, 0, 2))}"])
  rest_api_id = aws_api_gateway_rest_api.portal.id
  parent_id   = aws_api_gateway_resource.api.id
  path_part   = basename(each.key)
}

resource "aws_api_gateway_resource" "depth3" {
  for_each    = toset([for parts in local.path_segments : "/${join("/", slice(parts, 0, 3))}" if length(parts) >= 3])
  rest_api_id = aws_api_gateway_rest_api.portal.id
  parent_id   = aws_api_gateway_resource.depth2[dirname(each.key)].id
  path_part   = basename(each.key)
}

resource "aws_api_gateway_resource" "depth4" {
  for_each    = toset([for parts in local.path_segments : "/${join("/", slice(parts, 0, 4))}" if length(parts) >= 4])
  rest_api_id = aws_api_gateway_rest_api.portal.id
  parent_id   = aws_api_gateway_resource.depth3[dirname(each.key)].id
  path_part   = basename(each.key)
}

resource "aws_api_gateway_resource" "depth5" {
  for_each    = toset([for parts in local.path_segments : "/${join("/", parts)}" if length(parts) == 5])
  rest_api_id = aws_api_gateway_rest_api.portal.id
  parent_id   = aws_api_gateway_resource.depth4[dirname(each.key)].id
  path_part   = basename(each.key)
}

# 本体の経路（routes.json の main）も管理 Lambda に割り当てる
module "main_routes" {
  source              = "./modules/api_routes"
  route_group         = "main"
  authorization_scope = local.portal_api_scope
  rest_api_id         = aws_api_gateway_rest_api.portal.id
  authorizer_id       = aws_api_gateway_authorizer.human.id
  resource_ids        = local.api_resources
  lambda_invoke_arn   = aws_lambda_function.management.invoke_arn
  function_name       = aws_lambda_function.management.function_name
}

# 移動先は旧名のまま残し、modules/api_routes の moved で aws_lambda_permission.route へつなぐ
# （同じ移動先を持つ moved が 2 つあると Terraform が拒否するため）
moved {
  from = aws_lambda_permission.api
  to   = module.main_routes.aws_lambda_permission.management_api
}

# HTTP API の時期は管理 Lambda の経路を management_routes と呼んでいた。route_group が main であることに
# 名前を合わせた。適用済みの state は moved で引き継ぐ
moved {
  from = module.management_routes
  to   = module.main_routes
}

resource "aws_api_gateway_method" "preflight" {
  for_each           = local.route_paths
  rest_api_id        = aws_api_gateway_rest_api.portal.id
  resource_id        = local.api_resources[each.key]
  http_method        = "OPTIONS"
  authorization      = "NONE"
  request_parameters = { "method.request.header.Origin" = false }
}

resource "aws_api_gateway_integration" "preflight" {
  for_each                = aws_api_gateway_method.preflight
  rest_api_id             = aws_api_gateway_rest_api.portal.id
  resource_id             = each.value.resource_id
  http_method             = each.value.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.management.invoke_arn
}

resource "aws_lambda_permission" "preflight" {
  statement_id   = "AllowRestApiPreflight"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.management.function_name
  principal      = "apigateway.amazonaws.com"
  source_account = var.aws_account_id
  source_arn     = "${aws_api_gateway_rest_api.portal.execution_arn}/*/OPTIONS/api/*"
}

resource "aws_api_gateway_gateway_response" "cors" {
  for_each      = toset(["DEFAULT_4XX", "DEFAULT_5XX"])
  rest_api_id   = aws_api_gateway_rest_api.portal.id
  response_type = each.key
  response_parameters = {
    "gatewayresponse.header.Access-Control-Allow-Origin" = "method.request.header.Origin"
    "gatewayresponse.header.Vary"                        = "'Origin'"
  }
}

# 初回の stage 作成に使う。以後の snapshot と stage の切り替えは deploy.sh が行う（ADR-0015）。
resource "aws_api_gateway_deployment" "initial" {
  rest_api_id = aws_api_gateway_rest_api.portal.id
  depends_on = [
    aws_api_gateway_rest_api_policy.portal,
    aws_api_gateway_integration.preflight,
    aws_api_gateway_gateway_response.cors,
    module.main_routes,
  ]
}

resource "aws_api_gateway_stage" "portal" {
  rest_api_id   = aws_api_gateway_rest_api.portal.id
  deployment_id = aws_api_gateway_deployment.initial.id
  stage_name    = local.api_stage

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId = "$context.requestId", method = "$context.httpMethod", resourcePath = "$context.resourcePath",
      status    = "$context.status", responseLength = "$context.responseLength"
    })
  }

  lifecycle { ignore_changes = [deployment_id] }
}

resource "aws_api_gateway_method_settings" "portal" {
  rest_api_id = aws_api_gateway_rest_api.portal.id
  stage_name  = aws_api_gateway_stage.portal.stage_name
  method_path = "*/*"
  settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
}
