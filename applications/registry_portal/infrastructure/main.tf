data "aws_region" "current" {}

locals {
  # provider のリージョン（us-east-1 に固定。追加スタックのモジュールと同じく data source から読む）
  region = data.aws_region.current.region
  issuer = "https://cognito-idp.${local.region}.amazonaws.com/${var.cognito_user_pool_id}"
  # 管理 Lambda・実行ロール・ロググループの共通名。bootstrap の locals も同じ規則で組み立てる
  management_name = "${var.name_prefix}-management"
  spa_origin      = "https://${aws_cloudfront_distribution.spa.domain_name}"
  # HTTP API のすべての route（追加スタックを含む）が検証するカスタム scope。業務ロールの判定は handler が行う。
  # SPA の既定値（frontend/src/config.ts の PORTAL_API_SCOPE）と同じ値にする
  portal_api_scope_name = "api.invoke"
  portal_api_scope      = "${aws_cognito_resource_server.portal.identifier}/${local.portal_api_scope_name}"
  secret_region         = split(":", var.discovery_client_secret_arn)[3]
  log_group_arn         = "arn:aws:logs:${local.region}:${var.aws_account_id}:log-group:/aws/lambda/${local.management_name}"
  registry_record       = "${var.registry_arn}/record/*"
  # plan の段階で値が確定するよう、名前から組み立てる（追加スタックの supplement_table_arn にも使う）
  supplement_table_arn = "arn:aws:dynamodb:${local.region}:${var.aws_account_id}:table/${aws_dynamodb_table.auxiliary.name}"
}

resource "aws_cognito_resource_server" "portal" {
  identifier   = "registry-portal"
  name         = "Registry ポータル"
  user_pool_id = var.cognito_user_pool_id
  scope {
    scope_name        = local.portal_api_scope_name
    scope_description = "ポータルの API を呼び出す。route ごとに検証し、業務ロールの判定は handler が行う"
  }
}

resource "aws_cognito_user_pool_client" "human" {
  name                                 = "${var.name_prefix}-human"
  user_pool_id                         = var.cognito_user_pool_id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", local.portal_api_scope]
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
    attribute_name = "expiresAt"
    enabled        = true
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_cloudwatch_log_group" "management" {
  name              = "/aws/lambda/${local.management_name}"
  retention_in_days = 365
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${var.name_prefix}"
  retention_in_days = 365
}

resource "aws_iam_role" "management" {
  name                = local.management_name
  managed_policy_arns = []
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
  name = local.management_name
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
      },
      {
        # カタログ詳細が所有チームの正本と公開版のスナップショットを読む。下書きの権限は management スタックが付ける
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = local.supplement_table_arn
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
  function_name    = local.management_name
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
      SUPPLEMENT_TABLE_NAME       = aws_dynamodb_table.auxiliary.name
      # A2A_SEARCH_ENABLED は isA2A の補完と確認（#16）が終わるまで設定しない（未設定は無効）
    }
  }
  depends_on = [aws_iam_role_policy.management, aws_cloudwatch_log_group.management]
}

resource "aws_apigatewayv2_api" "portal" {
  name          = var.name_prefix
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = [local.spa_origin]
    # SPA は下書きの更新に PATCH、申請・決裁などに POST と Idempotency-Key を使う
    allow_methods = ["GET", "POST", "PATCH", "OPTIONS"]
    allow_headers = ["authorization", "content-type", "idempotency-key"]
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

# 本体スタックが管理 Lambda に割り当てる route と、Lambda の呼び出し許可の ID。
# 決裁と管理の追加 route は、それぞれの追加スタックが作る（modules/management_routes と同じ形の map）
locals {
  management_routes = {
    me = {
      route_key    = "GET /api/me"
      statement_id = "AllowHttpApiInvokeMe"
    }
    catalog = {
      route_key    = "GET /api/catalog"
      statement_id = "AllowHttpApiInvokeCatalog"
    }
    catalog_search = {
      route_key    = "POST /api/catalog/search"
      statement_id = "AllowHttpApiInvokeCatalogSearch"
    }
    catalog_detail = {
      route_key    = "GET /api/catalog/{recordId}"
      statement_id = "AllowHttpApiInvokeCatalogDetail"
    }
  }
  # 呼び出し許可の source_arn（execution ARN の stage の後ろ）は route_key から作る。
  # メソッドはそのまま、path は先頭の / を除き、{recordId} などのパスパラメータを * にする
  management_route_invoke_paths = {
    for name, route in local.management_routes :
    name => "${split(" ", route.route_key)[0]}/${replace(trimprefix(split(" ", route.route_key)[1], "/"), "/\\{[^}]+\\}/", "*")}"
  }
}

resource "aws_apigatewayv2_route" "management" {
  for_each             = local.management_routes
  api_id               = aws_apigatewayv2_api.portal.id
  route_key            = each.value.route_key
  target               = "integrations/${aws_apigatewayv2_integration.management.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.human.id
  authorization_scopes = [local.portal_api_scope]
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

# 呼び出し許可は本体スタックの route のメソッド（route_key の先頭）と path に限る
resource "aws_lambda_permission" "api" {
  for_each       = local.management_routes
  statement_id   = each.value.statement_id
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.management.function_name
  principal      = "apigateway.amazonaws.com"
  source_account = var.aws_account_id
  source_arn     = "${aws_apigatewayv2_api.portal.execution_arn}/*/${local.management_route_invoke_paths[each.key]}"
}
