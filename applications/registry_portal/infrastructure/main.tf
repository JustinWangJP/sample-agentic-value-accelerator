locals {
  issuer          = "https://cognito-idp.us-east-1.amazonaws.com/${var.cognito_user_pool_id}"
  spa_origin      = "https://${aws_cloudfront_distribution.spa.domain_name}"
  read_scope      = "registry-portal/catalog.read"
  secret_region   = split(":", var.discovery_client_secret_arn)[3]
  log_group_arn   = "arn:aws:logs:us-east-1:${var.aws_account_id}:log-group:/aws/lambda/${var.name_prefix}-management"
  registry_record = "${var.registry_arn}/record/*"
}

resource "aws_cognito_resource_server" "portal" {
  identifier   = "registry-portal"
  name         = "Registry ポータル"
  user_pool_id = var.cognito_user_pool_id
  scope {
    scope_name        = "catalog.read"
    scope_description = "ポータルの主体と公開承認済みカタログ項目を閲覧する"
  }
}

resource "aws_cognito_user_pool_client" "human" {
  name                                 = "${var.name_prefix}-human"
  user_pool_id                         = var.cognito_user_pool_id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "${aws_cognito_resource_server.portal.identifier}/catalog.read"]
  supported_identity_providers         = var.identity_providers
  callback_urls                        = ["${local.spa_origin}/"]
  logout_urls                          = ["${local.spa_origin}/"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 15
  id_token_validity                    = 15
  refresh_token_validity               = 1
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_group" "business" {
  for_each     = toset(["registry-publisher", "registry-curator", "registry-admin"])
  name         = each.value
  user_pool_id = var.cognito_user_pool_id
  description  = "Registry ポータルの業務ロール。IAM ロールは付けない。"
}

resource "aws_cognito_user_group" "team" {
  for_each     = var.team_names
  name         = "team-${each.value}"
  user_pool_id = var.cognito_user_pool_id
  description  = "Registry ポータルの所属チーム。"
}

resource "aws_dynamodb_table" "auxiliary" {
  name                        = "${var.name_prefix}-auxiliary"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = true
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  server_side_encryption { enabled = true }
  point_in_time_recovery { enabled = true }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_cloudwatch_log_group" "management" {
  name              = "/aws/lambda/${var.name_prefix}-management"
  retention_in_days = 365
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${var.name_prefix}"
  retention_in_days = 365
}

resource "aws_iam_role" "management" {
  name = "${var.name_prefix}-management"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "management" {
  role = aws_iam_role.management.name
  name = "${var.name_prefix}-management"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${local.log_group_arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["agent-registry:GetRegistry", "agent-registry:ListRegistryRecords"]
        Resource = var.registry_arn
      },
      {
        Effect   = "Allow"
        Action   = ["agent-registry:GetRegistryRecord"]
        Resource = local.registry_record
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.discovery_client_secret_arn
      }
      ], var.discovery_kms_key_arn == null ? [] : [{
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.discovery_kms_key_arn
        Condition = {
          StringEquals = { "kms:ViaService" = "secretsmanager.${local.secret_region}.amazonaws.com" }
          StringLike   = { "kms:EncryptionContext:SecretARN" = var.discovery_client_secret_arn }
        }
    }])
  })
}

resource "aws_lambda_function" "management" {
  function_name    = "${var.name_prefix}-management"
  role             = aws_iam_role.management.arn
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "registry_portal.handlers.management.handler"
  timeout          = 30
  memory_size      = 256
  environment {
    variables = {
      COGNITO_ISSUER              = local.issuer
      HUMAN_CLIENT_ID             = aws_cognito_user_pool_client.human.id
      REGISTRY_ARN                = var.registry_arn
      DISCOVERY_TOKEN_URL         = var.discovery_token_url
      DISCOVERY_CLIENT_ID         = var.discovery_client_id
      DISCOVERY_CLIENT_SECRET_ARN = var.discovery_client_secret_arn
      DISCOVERY_SCOPE             = var.discovery_scope
    }
  }
  depends_on = [aws_iam_role_policy.management, aws_cloudwatch_log_group.management]
}

resource "aws_apigatewayv2_api" "portal" {
  name          = var.name_prefix
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = [local.spa_origin]
    allow_methods = ["GET", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_authorizer" "human" {
  api_id           = aws_apigatewayv2_api.portal.id
  name             = "human-access-token"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.human.id]
    issuer   = local.issuer
  }
}

resource "aws_apigatewayv2_integration" "management" {
  api_id                 = aws_apigatewayv2_api.portal.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.management.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "management" {
  for_each             = toset(["GET /api/me", "GET /api/catalog"])
  api_id               = aws_apigatewayv2_api.portal.id
  route_key            = each.value
  target               = "integrations/${aws_apigatewayv2_integration.management.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.human.id
  authorization_scopes = [local.read_scope]
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.portal.id
  name        = "$default"
  auto_deploy = true
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId = "$context.requestId", routeKey = "$context.routeKey",
      status    = "$context.status", responseLength = "$context.responseLength"
    })
  }
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
}

resource "aws_lambda_permission" "api" {
  for_each       = toset(["me", "catalog"])
  statement_id   = "AllowApiGateway-${each.value}"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.management.function_name
  principal      = "apigateway.amazonaws.com"
  source_account = var.aws_account_id
  source_arn     = "${aws_apigatewayv2_api.portal.execution_arn}/*/GET/api/${each.value}"
}
