locals {
  api_stage            = "v1"
  cors_allowed_origins = distinct(concat([local.spa_origin], var.local_dev_origins))
  api_routes           = jsondecode(file("${path.module}/modules/management_routes/routes.json"))
  route_paths = toset(flatten([
    for routes in values(local.api_routes) : [for route in values(routes) : split(" ", route.route_key)[1]]
  ]))
  # 中間パスを含め、親が先に作られるよう階層ごとに resource を作る。
  path_segments = [for path in local.route_paths : split("/", trimprefix(path, "/"))]
  api_resources = merge(
    { "/api" = aws_api_gateway_resource.api.id },
    { for path, resource in aws_api_gateway_resource.collection : path => resource.id },
    { for path, resource in aws_api_gateway_resource.item : path => resource.id },
    { for path, resource in aws_api_gateway_resource.action : path => resource.id },
    { for path, resource in aws_api_gateway_resource.subaction : path => resource.id },
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
  provider_arns   = ["arn:aws:cognito-idp:${local.region}:${var.aws_account_id}:userpool/${var.cognito_user_pool_id}"]
}

resource "aws_api_gateway_resource" "api" {
  rest_api_id = aws_api_gateway_rest_api.portal.id
  parent_id   = aws_api_gateway_rest_api.portal.root_resource_id
  path_part   = "api"
}

resource "aws_api_gateway_resource" "collection" {
  for_each    = toset([for parts in local.path_segments : "/${join("/", slice(parts, 0, 2))}"])
  rest_api_id = aws_api_gateway_rest_api.portal.id
  parent_id   = aws_api_gateway_resource.api.id
  path_part   = basename(each.key)
}

resource "aws_api_gateway_resource" "item" {
  for_each    = toset([for parts in local.path_segments : "/${join("/", slice(parts, 0, 3))}" if length(parts) >= 3])
  rest_api_id = aws_api_gateway_rest_api.portal.id
  parent_id   = aws_api_gateway_resource.collection[dirname(each.key)].id
  path_part   = basename(each.key)
}

resource "aws_api_gateway_resource" "action" {
  for_each    = toset([for parts in local.path_segments : "/${join("/", slice(parts, 0, 4))}" if length(parts) >= 4])
  rest_api_id = aws_api_gateway_rest_api.portal.id
  parent_id   = aws_api_gateway_resource.item[dirname(each.key)].id
  path_part   = basename(each.key)
}

resource "aws_api_gateway_resource" "subaction" {
  for_each    = toset([for parts in local.path_segments : "/${join("/", parts)}" if length(parts) == 5])
  rest_api_id = aws_api_gateway_rest_api.portal.id
  parent_id   = aws_api_gateway_resource.action[dirname(each.key)].id
  path_part   = basename(each.key)
}

module "management_routes" {
  source                   = "./modules/management_routes"
  routes                   = local.api_routes.main
  authorization_scope      = local.portal_api_scope
  rest_api_id              = aws_api_gateway_rest_api.portal.id
  authorizer_id            = aws_api_gateway_authorizer.human.id
  resource_ids             = local.api_resources
  lambda_invoke_arn        = aws_lambda_function.management.invoke_arn
  management_function_name = aws_lambda_function.management.function_name
}

moved {
  from = aws_lambda_permission.api
  to   = module.management_routes.aws_lambda_permission.management_api
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
    module.management_routes,
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
