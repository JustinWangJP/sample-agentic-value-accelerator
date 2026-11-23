data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  # provider のリージョン（us-east-1 に固定。追加スタックのモジュールと同じく data source から読む）
  region = data.aws_region.current.region
  # ARN のパーティション。REST API の Cognito authorizer（rest_api.tf）が使う。経路モジュール（modules/api_routes）と同じく data source から読む
  partition = data.aws_partition.current.partition
  issuer    = "https://cognito-idp.${local.region}.amazonaws.com/${var.cognito_user_pool_id}"
  # 管理 Lambda・実行ロール・ロググループの共通名。bootstrap の locals も同じ規則で組み立てる
  management_name = "${var.name_prefix}-management"
  spa_origin      = "https://${aws_cloudfront_distribution.spa.domain_name}"
  # 人間用 client のコールバック URL・ログアウト URL。CloudFront の SPA と、ローカルの開発サーバー（var.local_dev_origins）
  spa_redirect_urls = concat(["${local.spa_origin}/"], [for o in var.local_dev_origins : "${o}/"])
  # REST API のすべての route（追加スタックを含む）が検証するカスタム scope。業務ロールの判定は handler が行う。
  # 追加スタックと SPA には output（rest_api と frontend_config）で渡し、値はここだけに定義する
  portal_api_scope_name = "api.invoke"
  portal_api_scope      = "${aws_cognito_resource_server.portal.identifier}/${local.portal_api_scope_name}"
  registry_record       = "${var.registry_arn}/record/*"
  # plan の段階で値が確定するよう、名前から組み立てる（追加スタックの supplement_table_arn にも使う）
  supplement_table_arn = "arn:aws:dynamodb:${local.region}:${var.aws_account_id}:table/${aws_dynamodb_table.auxiliary.name}"
}

resource "aws_cognito_resource_server" "portal" {
  identifier   = "registry-portal"
  name         = "Registry Portal"
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
  callback_urls                        = local.spa_redirect_urls
  logout_urls                          = local.spa_redirect_urls
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
  deletion_protection_enabled = var.table_deletion_protection
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

module "management_log_group" {
  source            = "./modules/lambda_log_group"
  function_name     = local.management_name
  retention_in_days = var.log_retention_in_days
}

# ロググループを追加スタックと共通のモジュールに移した。適用済みの state は moved で引き継ぐ
moved {
  from = aws_cloudwatch_log_group.management
  to   = module.management_log_group.aws_cloudwatch_log_group.this
}

# 発見用 M2M client の環境変数と、顧客管理 KMS キーの場合の kms:Decrypt（decision スタックと共通）
module "discovery_client" {
  source = "./modules/discovery_client"
  client = {
    token_url         = var.discovery_token_url
    client_id         = var.discovery_client_id
    client_secret_arn = var.discovery_client_secret_arn
    scope             = var.discovery_scope
  }
  kms_key_arn = var.discovery_kms_key_arn
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${var.name_prefix}"
  retention_in_days = var.log_retention_in_days
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
        Resource = "${module.management_log_group.arn}:*"
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
    ], module.discovery_client.kms_statements)
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
  # null なら属性を設定せず、予約しない（既定の動作のまま）
  reserved_concurrent_executions = var.lambda_reserved_concurrency
  environment {
    # A2A_SEARCH_ENABLED は isA2A の補完と確認（#16）が終わるまで設定しない（未設定は無効）
    variables = merge(module.discovery_client.environment, {
      CORS_ALLOWED_ORIGINS  = jsonencode(local.cors_allowed_origins)
      COGNITO_ISSUER        = local.issuer
      HUMAN_CLIENT_ID       = aws_cognito_user_pool_client.human.id
      REGISTRY_ARN          = var.registry_arn
      SUPPLEMENT_TABLE_NAME = aws_dynamodb_table.auxiliary.name
    })
  }
  depends_on = [aws_iam_role_policy.management, module.management_log_group]
}
