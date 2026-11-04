mock_provider "aws" {
  mock_resource "aws_cloudfront_distribution" {
    defaults = { domain_name = "portal.example.cloudfront.net" }
  }
}

variables {
  aws_account_id              = "123456789012"
  spa_bucket_name             = "portal-test-123456789012"
  registry_arn                = "arn:aws:agent-registry:us-east-1:123456789012:registry/AbCdEf123456"
  cognito_user_pool_id        = "us-east-1_Example"
  cognito_domain              = "https://example.auth.us-east-1.amazoncognito.com"
  discovery_token_url         = "https://example.auth.us-east-1.amazoncognito.com/oauth2/token"
  discovery_client_id         = "discovery-client"
  discovery_client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:discovery-AbCdEf"
  lambda_zip_path             = "tests/fixtures/safe.json"
}

run "security_boundaries" {
  command = plan
  assert {
    condition     = aws_s3_bucket_public_access_block.spa.block_public_acls && aws_s3_bucket_public_access_block.spa.block_public_policy && aws_s3_bucket_public_access_block.spa.ignore_public_acls && aws_s3_bucket_public_access_block.spa.restrict_public_buckets
    error_message = "SPA バケットの公開アクセスを全面的に禁止してください。"
  }
  assert {
    condition     = aws_cloudfront_origin_access_control.spa.signing_behavior == "always" && aws_cloudfront_origin_access_control.spa.signing_protocol == "sigv4"
    error_message = "S3 は OAC の署名で取得してください。"
  }
  assert {
    condition     = aws_cognito_user_pool_client.human.generate_secret == false && aws_cognito_user_pool_client.human.allowed_oauth_flows == toset(["code"]) && contains(aws_cognito_user_pool_client.human.allowed_oauth_scopes, "registry-portal/catalog.read")
    error_message = "人間用 client は secret のない code flow とカスタム scope が必要です。"
  }
  assert {
    condition     = alltrue([for route in aws_apigatewayv2_route.management : route.authorization_type == "JWT" && route.authorization_scopes == toset(["registry-portal/catalog.read"])])
    error_message = "すべての route に人間用 JWT と scope の検証が必要です。"
  }
  assert {
    condition     = aws_dynamodb_table.auxiliary.billing_mode == "PAY_PER_REQUEST" && aws_dynamodb_table.auxiliary.deletion_protection_enabled && aws_dynamodb_table.auxiliary.point_in_time_recovery[0].enabled && aws_dynamodb_table.auxiliary.server_side_encryption[0].enabled
    error_message = "補助テーブルの暗号化・PITR・削除保護を有効にしてください。"
  }
  assert {
    condition     = aws_cloudwatch_log_group.management.retention_in_days == 365 && aws_cloudwatch_log_group.api.retention_in_days == 365
    error_message = "ログは365日保存してください。"
  }
  assert {
    condition     = aws_lambda_function.management.environment[0].variables["REGISTRY_ARN"] == var.registry_arn && aws_lambda_function.management.environment[0].variables["DISCOVERY_CLIENT_SECRET_ARN"] == var.discovery_client_secret_arn
    error_message = "管理 handler に固定した Registry と secret ARN を渡してください。"
  }
  assert {
    condition     = length(aws_lambda_function.management.vpc_config) == 0
    error_message = "MVP1 の Lambda は VPC 外で実行してください。"
  }
  assert {
    condition     = length(aws_cloudfront_distribution.spa.custom_error_response) == 0
    error_message = "エラーを index.html に差し替える設定を追加できません。"
  }
  assert {
    condition     = strcontains(aws_cloudfront_response_headers_policy.spa.security_headers_config[0].content_security_policy[0].content_security_policy, var.cognito_domain) && strcontains(aws_cloudfront_response_headers_policy.spa.security_headers_config[0].content_security_policy[0].content_security_policy, "https://*.execute-api.us-east-1.amazonaws.com")
    error_message = "CSP は Cognito token と HTTP API への通信を許可してください。"
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.management.policy, "kms:Decrypt") && strcontains(aws_iam_role_policy.management.policy, var.discovery_client_secret_arn)
    error_message = "secret は完全な ARN に限定し、AWS 管理キーの場合は KMS 権限を付けないでください。"
  }
}

run "customer_managed_secret_key" {
  command = plan
  variables {
    discovery_kms_key_arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
  }
  assert {
    condition     = strcontains(aws_iam_role_policy.management.policy, "kms:Decrypt") && strcontains(aws_iam_role_policy.management.policy, "secretsmanager.us-east-1.amazonaws.com") && strcontains(aws_iam_role_policy.management.policy, "kms:EncryptionContext:SecretARN")
    error_message = "顧客管理キーの復号は対象 secret と Secrets Manager 経由に限定してください。"
  }
}
