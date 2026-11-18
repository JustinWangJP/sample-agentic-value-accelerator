mock_provider "aws" {
  mock_resource "aws_cloudfront_distribution" {
    defaults = { domain_name = "portal.example.cloudfront.net", arn = "arn:aws:cloudfront::123456789012:distribution/EXAMPLE" }
  }
  # apply で ARN の形式を検証する属性には、形式の正しい値を与える
  mock_resource "aws_cloudfront_function" {
    defaults = { arn = "arn:aws:cloudfront::123456789012:function/registry-portal-dev-spa" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/example" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/registry-portal-dev-management" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::portal-test-123456789012" }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn        = "arn:aws:lambda:us-east-1:123456789012:function:registry-portal-dev-management"
      invoke_arn = "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-1:123456789012:function:registry-portal-dev-management/invocations"
    }
  }
  mock_resource "aws_apigatewayv2_api" {
    defaults = {
      id            = "abcde12345"
      api_endpoint  = "https://abcde12345.execute-api.us-east-1.amazonaws.com"
      execution_arn = "arn:aws:execute-api:us-east-1:123456789012:abcde12345"
    }
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
    condition     = length(aws_iam_role.management.managed_policy_arns) == 0
    error_message = "管理 Lambda には managed policy を付与せず、空集合を plan で確定してください。"
  }
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
    condition     = toset([for route in aws_apigatewayv2_route.management : route.route_key]) == toset(["GET /api/me", "GET /api/catalog", "POST /api/catalog/search", "GET /api/catalog/{recordId}"])
    error_message = "本体スタックの route は主体とカタログ（一覧・検索・詳細）だけにしてください。決裁と管理の追加 route は追加スタックが作ります。"
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
    condition = (
      aws_apigatewayv2_api.portal.cors_configuration[0].allow_methods == toset(["GET", "POST", "PATCH", "OPTIONS"])
      && aws_apigatewayv2_api.portal.cors_configuration[0].allow_headers == toset(["authorization", "content-type", "idempotency-key"])
      && aws_apigatewayv2_api.portal.cors_configuration[0].allow_credentials != true
    )
    error_message = "CORS は SPA が使う GET・POST・PATCH と、Authorization・Content-Type・Idempotency-Key ヘッダーだけを許可してください。"
  }
  assert {
    condition     = aws_dynamodb_table.auxiliary.ttl[0].enabled && aws_dynamodb_table.auxiliary.ttl[0].attribute_name == "expiresAt"
    error_message = "補助テーブルの TTL 属性はアプリ（services/dynamodb.py）と同じ expiresAt にしてください。"
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
    condition = (
      aws_lambda_function.management.environment[0].variables["SUPPLEMENT_TABLE_NAME"] == "registry-portal-dev-auxiliary"
      && aws_lambda_function.management.environment[0].variables["DISCOVERY_TOKEN_URL"] == var.discovery_token_url
      && aws_lambda_function.management.environment[0].variables["DISCOVERY_CLIENT_ID"] == var.discovery_client_id
      && aws_lambda_function.management.environment[0].variables["DISCOVERY_SCOPE"] == ""
    )
    error_message = "管理 handler に補助テーブル名（SUPPLEMENT_TABLE_NAME）と発見用 M2M の設定を渡してください。"
  }
  assert {
    condition     = !contains(keys(aws_lambda_function.management.environment[0].variables), "A2A_SEARCH_ENABLED")
    error_message = "A2A 検索は isA2A の準備が終わるまで無効にするため、A2A_SEARCH_ENABLED を設定しないでください。"
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
  assert {
    condition = anytrue([
      for statement in jsondecode(aws_iam_role_policy.management.policy).Statement :
      statement.Action == ["dynamodb:GetItem"] && statement.Resource == "arn:aws:dynamodb:us-east-1:123456789012:table/registry-portal-dev-auxiliary"
    ])
    error_message = "管理 Lambda のカタログ詳細には、補助テーブルに限った dynamodb:GetItem が必要です。"
  }
  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.management.policy).Statement :
      alltrue([for action in flatten([statement.Action]) : !startswith(action, "dynamodb:") || action == "dynamodb:GetItem"])
    ])
    error_message = "本体スタックが管理 Lambda に付ける DynamoDB 権限は GetItem だけにしてください（下書きの権限は management スタックが付ける）。"
  }
}

run "lambda_invoke_permissions" {
  command = apply
  assert {
    condition = toset([for permission in aws_lambda_permission.api : trimprefix(permission.source_arn, "${aws_apigatewayv2_api.portal.execution_arn}/*/")]) == toset([
      "GET/api/me", "GET/api/catalog", "POST/api/catalog/search", "GET/api/catalog/*",
    ])
    error_message = "管理 Lambda の呼び出し許可は、本体スタックの route のメソッドと path に限ってください。"
  }
  assert {
    condition     = alltrue([for permission in aws_lambda_permission.api : permission.principal == "apigateway.amazonaws.com" && permission.source_account == var.aws_account_id])
    error_message = "管理 Lambda は同じアカウントの API Gateway からだけ呼び出せるようにしてください。"
  }
}

run "outputs_for_additional_stacks" {
  command = apply
  assert {
    condition = (
      output.http_api_id == "abcde12345"
      && output.jwt_authorizer_id == aws_apigatewayv2_authorizer.human.id
      && output.management_integration_id == aws_apigatewayv2_integration.management.id
      && output.human_client_id == aws_cognito_user_pool_client.human.id
      && output.cognito_issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Example"
    )
    error_message = "追加スタックが HTTP API・JWT authorizer・管理 integration・人間用 client を参照できるよう、ID を出力してください。"
  }
  assert {
    condition = (
      output.management_role_name == "registry-portal-dev-management"
      && output.management_function_name == "registry-portal-dev-management"
      && output.supplement_table_name == "registry-portal-dev-auxiliary"
      && output.supplement_table_arn == "arn:aws:dynamodb:us-east-1:123456789012:table/registry-portal-dev-auxiliary"
    )
    error_message = "追加スタックの tfvars に転記する管理ロール名・関数名と補助テーブルの名前・ARN を出力してください。"
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
