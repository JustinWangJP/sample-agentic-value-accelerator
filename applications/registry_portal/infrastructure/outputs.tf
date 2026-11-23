output "spa_origin" {
  description = "SPA の HTTPS origin。CORS と人間用 client の callback URL に使用する。"
  value       = local.spa_origin
}
output "spa_bucket_name" {
  description = "ビルドした SPA の同期先。"
  value       = aws_s3_bucket.spa.id
}
output "cloudfront_distribution_id" {
  description = "配信後のキャッシュ無効化先。"
  value       = aws_cloudfront_distribution.spa.id
}
output "frontend_config" {
  description = "SPA のビルド時設定。secret を含まない。"
  value = {
    VITE_COGNITO_DOMAIN    = var.cognito_domain
    VITE_COGNITO_CLIENT_ID = aws_cognito_user_pool_client.human.id
    VITE_REDIRECT_URI      = "${local.spa_origin}/"
    VITE_API_BASE_URL      = aws_api_gateway_stage.portal.invoke_url
    VITE_COGNITO_SCOPES    = "openid ${local.portal_api_scope}"
  }
}

# 以下は追加スタック（management・decision・notification_events）の terraform.tfvars に転記する値。
# 追加スタックは別 state のまま運用し、本体スタックの資源を ID と名前で借用する。
output "rest_api" {
  description = "追加スタックが経路を割り当てる REST API（management・decision の rest_api にそのまま転記する）。ID・ステージ名（deploy.sh の再デプロイ先）・Cognito User Pools authorizer の ID・経路が検証するポータル用のカスタム scope・管理・決裁 Lambda の CORS 許可リスト。"
  value = {
    id                   = aws_api_gateway_rest_api.portal.id
    stage_name           = aws_api_gateway_stage.portal.stage_name
    authorizer_id        = aws_api_gateway_authorizer.human.id
    portal_api_scope     = local.portal_api_scope
    cors_allowed_origins = local.cors_allowed_origins
  }
}
output "api_region" {
  description = "REST API のリージョン。deploy.sh で本体スタックを再デプロイするときに使用する（追加スタックは自身の region を使う）。"
  value       = local.region
}
output "human_client_id" {
  description = "人間用の公開 SPA client の ID（decision の human_client_id）。"
  value       = aws_cognito_user_pool_client.human.id
}
output "cognito_issuer" {
  description = "人間用トークンの issuer（decision の cognito_issuer）。"
  value       = local.issuer
}
output "management_role_name" {
  description = "管理 Lambda の実行ロール名（management の management_role_name）。"
  value       = aws_iam_role.management.name
}
output "management_function_name" {
  description = "管理 Lambda の関数名（management の management_function_name）。"
  value       = aws_lambda_function.management.function_name
}
output "supplement_table_name" {
  description = "補助 DynamoDB テーブル名（decision・notification_events の supplement_table_name）。"
  value       = aws_dynamodb_table.auxiliary.name
}
output "supplement_table_arn" {
  description = "補助 DynamoDB テーブルの ARN（各追加スタックの supplement_table_arn）。"
  value       = local.supplement_table_arn
}
