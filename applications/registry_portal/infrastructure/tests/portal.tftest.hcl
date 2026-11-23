mock_provider "aws" {
  # provider のリージョン（versions.tf で us-east-1 に固定）
  mock_data "aws_region" {
    defaults = { region = "us-east-1" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_resource "aws_api_gateway_stage" {
    defaults = { invoke_url = "https://abcde12345.execute-api.us-east-1.amazonaws.com/v1" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
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
  mock_resource "aws_api_gateway_rest_api" {
    defaults = {
      id            = "abcde12345"
      execution_arn = "arn:aws:execute-api:us-east-1:123456789012:abcde12345"
    }
  }
}

variables {
  allowed_source_cidrs        = ["203.0.113.10/32"]
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
    condition     = can(regex("^[\\w\\s+=,.@-]{1,256}$", aws_cognito_resource_server.portal.name))
    error_message = "Cognito のリソースサーバー表示名は、API が許可する文字で1〜256文字にしてください。"
  }
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
    condition     = aws_cognito_user_pool_client.human.generate_secret == false && aws_cognito_user_pool_client.human.allowed_oauth_flows == toset(["code"]) && contains(aws_cognito_user_pool_client.human.allowed_oauth_scopes, "registry-portal/api.invoke")
    error_message = "人間用 client は secret のない code flow とカスタム scope が必要です。"
  }
  assert {
    condition     = toset([for route in values(module.main_routes.routes) : route.route_key]) == toset(["GET /api/me", "GET /api/catalog", "POST /api/catalog/search", "GET /api/catalog/{recordId}"])
    error_message = "本体スタックの route は主体とカタログ（一覧・検索・詳細）だけにしてください。決裁と管理の追加 route は追加スタックが作ります。"
  }
  assert {
    condition     = alltrue([for route in values(module.main_routes.routes) : route.authorization_type == "COGNITO_USER_POOLS" && route.authorization_scopes == toset(["registry-portal/api.invoke"])])
    error_message = "すべての route で、Cognito User Pools authorizer によるアクセストークンと scope の検証が必要です。"
  }
  assert {
    condition     = aws_dynamodb_table.auxiliary.billing_mode == "PAY_PER_REQUEST" && aws_dynamodb_table.auxiliary.deletion_protection_enabled && aws_dynamodb_table.auxiliary.point_in_time_recovery[0].enabled && aws_dynamodb_table.auxiliary.server_side_encryption[0].enabled
    error_message = "補助テーブルの暗号化・PITR・削除保護を有効にしてください。"
  }
  assert {
    condition = (
      toset(aws_api_gateway_rest_api.portal.endpoint_configuration[0].types) == toset(["REGIONAL"])
      && aws_api_gateway_authorizer.human.type == "COGNITO_USER_POOLS"
      && toset(jsondecode(aws_api_gateway_rest_api_policy.portal.policy).Statement[1].Condition.NotIpAddress["aws:SourceIp"]) == toset(var.allowed_source_cidrs)
      && jsondecode(aws_api_gateway_rest_api_policy.portal.policy).Statement[1].Effect == "Deny"
    )
    error_message = "REST API は REGIONAL と Cognito authorizer を使用し、許可リスト外の接続元を明示的に拒否してください。"
  }
  assert {
    condition = (
      keys(aws_api_gateway_method.preflight) == keys(aws_api_gateway_integration.preflight)
      # 経路表（routes.json）の全グループのパスごとに 1 つ。追加スタックの経路のパスも本体が作る
      && toset(keys(aws_api_gateway_method.preflight)) == toset(flatten([for group in values(jsondecode(file("modules/api_routes/routes.json"))) : [for route in values(group) : route.path]]))
      && alltrue([for method in aws_api_gateway_method.preflight : method.http_method == "OPTIONS" && method.authorization == "NONE"])
      && alltrue([for integration in aws_api_gateway_integration.preflight : integration.type == "AWS_PROXY"])
      && alltrue([for response in aws_api_gateway_gateway_response.cors : response.response_parameters["gatewayresponse.header.Access-Control-Allow-Origin"] == "method.request.header.Origin"])
    )
    error_message = "各パスに認証不要の OPTIONS を作成し、API Gateway のエラーに CORS ヘッダーを付けてください。"
  }
  assert {
    condition     = aws_dynamodb_table.auxiliary.ttl[0].enabled && aws_dynamodb_table.auxiliary.ttl[0].attribute_name == "expiresAt"
    error_message = "補助テーブルの TTL 属性はアプリ（services/dynamodb.py）と同じ expiresAt にしてください。"
  }
  assert {
    # 既定値（log_retention_in_days を指定しない）では、今までどおり 365 日保存する
    condition     = module.management_log_group.name == "/aws/lambda/registry-portal-dev-management" && aws_cloudwatch_log_group.api.retention_in_days == 365 && module.management_log_group.retention_in_days == 365
    error_message = "既定ではログを365日保存し、管理 Lambda のロググループは共通のモジュールで作ってください。"
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
    error_message = "CSP は Cognito token と REST API への通信を許可してください。"
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
    condition = toset([for permission in values(module.main_routes.invoke_permissions) : trimprefix(permission.source_arn, "${aws_api_gateway_rest_api.portal.execution_arn}/*/")]) == toset([
      "GET/api/me", "GET/api/catalog", "POST/api/catalog/search", "GET/api/catalog/*",
    ])
    error_message = "管理 Lambda の呼び出し許可は、本体スタックの route のメソッドと path に限ってください。"
  }
  assert {
    # route と呼び出し許可を同じ map（{route_key, statement_id}）から作り、キーとメソッドを対応させる
    condition = keys(module.main_routes.routes) == keys(module.main_routes.invoke_permissions) && alltrue([
      for name, route in module.main_routes.routes :
      startswith(module.main_routes.invoke_permissions[name].source_arn, "${aws_api_gateway_rest_api.portal.execution_arn}/*/${split(" ", route.route_key)[0]}/")
    ])
    error_message = "本体スタックの route と呼び出し許可は、同じキーの 1 つの map から作り、メソッドを一致させてください。"
  }
  assert {
    condition     = alltrue([for permission in values(module.main_routes.invoke_permissions) : permission.principal == "apigateway.amazonaws.com" && permission.source_account == var.aws_account_id && permission.function_name == aws_lambda_function.management.function_name])
    error_message = "管理 Lambda は同じアカウントの API Gateway からだけ呼び出せるようにしてください。"
  }
}

run "outputs_for_additional_stacks" {
  command = apply
  assert {
    condition = (
      output.rest_api.id == "abcde12345"
      && output.rest_api.authorizer_id == aws_api_gateway_authorizer.human.id
      && output.rest_api.stage_name == "v1"
      && output.rest_api.cors_allowed_origins == tolist(["https://portal.example.cloudfront.net"])
      && output.frontend_config.VITE_API_BASE_URL == "https://abcde12345.execute-api.us-east-1.amazonaws.com/v1"
      && output.human_client_id == aws_cognito_user_pool_client.human.id
      && output.cognito_issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Example"
    )
    error_message = "追加スタックが REST API・Cognito User Pools authorizer・人間用 client を参照できるよう、ID を出力してください。"
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
  assert {
    condition     = output.frontend_config.VITE_COGNITO_SCOPES == "openid ${output.rest_api.portal_api_scope}" && output.rest_api.portal_api_scope == "registry-portal/api.invoke"
    error_message = "SPA は route が検証するカスタム scope を含めてトークンを要求し、追加スタックに渡す scope を出力してください。"
  }
  assert {
    # management・decision の variable "rest_api" と同じ属性にし、output をそのまま転記できるようにする
    condition     = toset(keys(output.rest_api)) == toset(["id", "stage_name", "authorizer_id", "portal_api_scope", "cors_allowed_origins"])
    error_message = "output rest_api は、追加スタックの変数 rest_api と同じ属性で出力してください。"
  }
}

run "customer_managed_secret_key" {
  command = plan
  variables {
    discovery_kms_key_arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
  }
  assert {
    condition = [
      for statement in jsondecode(aws_iam_role_policy.management.policy).Statement : statement.Condition
      if statement.Action == ["kms:Decrypt"]
      ] == [{
        StringEquals = {
          "kms:ViaService"                  = "secretsmanager.us-east-1.amazonaws.com"
          "kms:EncryptionContext:SecretARN" = var.discovery_client_secret_arn
        }
    }]
    error_message = "顧客管理キーの復号は、Secrets Manager 経由と対象 secret の完全一致（StringEquals）に限定してください。"
  }
}

# ログの保持期間（#62）。dev では dev.tfvars で短くし、API と管理 Lambda のロググループの両方に効かせる
run "log_retention_dev_value" {
  command = plan
  variables {
    log_retention_in_days = 14
  }
  assert {
    condition     = aws_cloudwatch_log_group.api.retention_in_days == 14 && module.management_log_group.retention_in_days == 14
    error_message = "log_retention_in_days の値を、API と管理 Lambda のロググループの保持期間に使ってください。"
  }
}

run "log_retention_rejects_unsupported_value" {
  command = plan
  variables {
    log_retention_in_days = 10
  }
  expect_failures = [var.log_retention_in_days]
}

run "teardown_protection_defaults" {
  command = plan
  assert {
    condition     = aws_dynamodb_table.auxiliary.deletion_protection_enabled == true
    error_message = "既定値では補助テーブルの削除保護を有効にしてください。無効にするのは撤去のときだけです。"
  }
  assert {
    condition     = aws_s3_bucket.spa.force_destroy == false
    error_message = "既定値では SPA バケットの force_destroy を false にしてください。true にするのは撤去のときだけです。"
  }
}

run "teardown_switches" {
  command = plan
  variables {
    table_deletion_protection = false
    spa_force_destroy         = true
  }
  assert {
    condition     = aws_dynamodb_table.auxiliary.deletion_protection_enabled == false
    error_message = "table_deletion_protection = false で補助テーブルの削除保護を外せるようにしてください。"
  }
  assert {
    condition     = aws_s3_bucket.spa.force_destroy == true
    error_message = "spa_force_destroy = true で SPA バケットを中身ごと削除できるようにしてください。"
  }
}

run "spa_noncurrent_versions_kept_by_default" {
  command = plan
  assert {
    condition     = length(aws_s3_bucket_lifecycle_configuration.spa) == 0
    error_message = "既定値では SPA バケットにライフサイクル設定を作らず、古いバージョンを残してください。"
  }
}

run "spa_noncurrent_versions_expire_in_dev" {
  command = plan
  variables {
    # dev.tfvars の推奨値
    spa_noncurrent_version_expiration_days = 7
  }
  assert {
    condition     = length(aws_s3_bucket_lifecycle_configuration.spa) == 1 && length(aws_s3_bucket_lifecycle_configuration.spa[0].rule) == 1
    error_message = "日数を指定した場合は、SPA バケットにライフサイクル設定を 1 つ（規則 1 つ）作ってください。"
  }
  assert {
    condition     = aws_s3_bucket_lifecycle_configuration.spa[0].rule[0].status == "Enabled" && aws_s3_bucket_lifecycle_configuration.spa[0].rule[0].noncurrent_version_expiration[0].noncurrent_days == 7
    error_message = "古いバージョンは指定した日数（dev では 7 日）で消してください。"
  }
  assert {
    condition     = aws_s3_bucket_lifecycle_configuration.spa[0].rule[0].abort_incomplete_multipart_upload[0].days_after_initiation == 1
    error_message = "未完了のマルチパートアップロードは 1 日で中止してください。"
  }
  assert {
    # prefix は provider が plan 時に未確定（computed）にするため、条件を絞る要素（tag・and）がないことで確かめる
    condition     = length(aws_s3_bucket_lifecycle_configuration.spa[0].rule[0].filter) == 1 && length(aws_s3_bucket_lifecycle_configuration.spa[0].rule[0].filter[0].tag) == 0 && length(aws_s3_bucket_lifecycle_configuration.spa[0].rule[0].filter[0].and) == 0
    error_message = "規則の filter は空にして、バケット全体を対象にしてください。"
  }
}

run "spa_noncurrent_version_expiration_days_must_be_positive_integer" {
  command = plan
  variables {
    spa_noncurrent_version_expiration_days = 0
  }
  expect_failures = [var.spa_noncurrent_version_expiration_days]
}

# 予約同時実行数（#61 実装案 5）。既定値では属性を設定せず（予約なし）、今の動作と差分を出さない
run "lambda_reserved_concurrency_default" {
  command = plan
  assert {
    condition     = aws_lambda_function.management.reserved_concurrent_executions == null
    error_message = "lambda_reserved_concurrency を指定しないときは、管理 Lambda の同時実行数を予約しないでください。"
  }
}

run "lambda_reserved_concurrency_set" {
  command = plan
  variables {
    lambda_reserved_concurrency = 5
  }
  assert {
    condition     = aws_lambda_function.management.reserved_concurrent_executions == 5
    error_message = "lambda_reserved_concurrency を指定したら、管理 Lambda の reserved_concurrent_executions に渡してください。"
  }
}

run "local_dev_origins_default" {
  command = plan
  assert {
    condition = (
      aws_cognito_user_pool_client.human.callback_urls == toset(["https://portal.example.cloudfront.net/"])
      && aws_cognito_user_pool_client.human.logout_urls == toset(["https://portal.example.cloudfront.net/"])
      && toset(jsondecode(aws_lambda_function.management.environment[0].variables.CORS_ALLOWED_ORIGINS)) == toset(["https://portal.example.cloudfront.net"])
    )
    error_message = "既定値（local_dev_origins = []）では、コールバック URL・ログアウト URL・CORS に CloudFront の SPA の origin だけを許可してください。"
  }
}

run "local_dev_origins_added" {
  command = plan
  variables {
    local_dev_origins = ["http://localhost:5173"]
  }
  assert {
    condition = (
      aws_cognito_user_pool_client.human.callback_urls == toset(["https://portal.example.cloudfront.net/", "http://localhost:5173/"])
      && aws_cognito_user_pool_client.human.logout_urls == toset(["https://portal.example.cloudfront.net/", "http://localhost:5173/"])
    )
    error_message = "local_dev_origins の origin を、末尾に / を付けて人間用 client のコールバック URL とログアウト URL に追加してください。"
  }
  assert {
    condition     = toset(jsondecode(aws_lambda_function.management.environment[0].variables.CORS_ALLOWED_ORIGINS)) == toset(["https://portal.example.cloudfront.net", "http://localhost:5173"])
    error_message = "local_dev_origins の origin を、REST API の CORS で許可する origin に追加してください。"
  }
}

run "local_dev_origins_accepts_loopback_address" {
  command = plan
  variables {
    local_dev_origins = ["http://127.0.0.1:4173"]
  }
  assert {
    condition     = contains(aws_cognito_user_pool_client.human.callback_urls, "http://127.0.0.1:4173/") && contains(toset(jsondecode(aws_lambda_function.management.environment[0].variables.CORS_ALLOWED_ORIGINS)), "http://127.0.0.1:4173")
    error_message = "http://127.0.0.1:<port> の形の origin も受け付けてください。"
  }
}

run "local_dev_origins_rejects_non_localhost" {
  command = plan
  variables {
    local_dev_origins = ["https://example.com"]
  }
  expect_failures = [var.local_dev_origins]
}

run "local_dev_origins_rejects_trailing_slash" {
  command = plan
  variables {
    local_dev_origins = ["http://localhost:5173/"]
  }
  expect_failures = [var.local_dev_origins]
}

run "local_dev_origins_rejects_lookalike_host" {
  command = plan
  variables {
    local_dev_origins = ["http://localhost.example.com:5173"]
  }
  expect_failures = [var.local_dev_origins]
}

run "allowed_source_cidrs_rejects_empty" {
  command = plan
  variables {
    allowed_source_cidrs = []
  }
  expect_failures = [var.allowed_source_cidrs]
}

run "allowed_source_cidrs_rejects_invalid" {
  command = plan
  variables {
    allowed_source_cidrs = ["203.0.113.10/32", "not-a-cidr"]
  }
  expect_failures = [var.allowed_source_cidrs]
}
