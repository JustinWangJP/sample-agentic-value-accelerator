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
    VITE_API_BASE_URL      = aws_apigatewayv2_api.portal.api_endpoint
    VITE_COGNITO_SCOPES    = "openid ${local.portal_api_scope}"
  }
}

# 以下は追加スタック（management・decision・notification_events）の terraform.tfvars に転記する値。
# 追加スタックは別 state のまま運用し、本体スタックの資源を ID と名前で借用する。
output "http_api_id" {
  description = "追加スタックが route を割り当てる HTTP API の ID（management・decision の http_api_id）。"
  value       = aws_apigatewayv2_api.portal.id
}
output "portal_api_scope" {
  description = "HTTP API の route が JWT authorizer で検証するポータル用のカスタム scope（management・decision の portal_api_scope）。"
  value       = local.portal_api_scope
}
output "jwt_authorizer_id" {
  description = "人間用 SPA client のトークンを検証する JWT authorizer の ID（management・decision の jwt_authorizer_id）。"
  value       = aws_apigatewayv2_authorizer.human.id
}
output "management_integration_id" {
  description = "管理 Lambda の HTTP API 統合の ID（management の management_integration_id）。"
  value       = aws_apigatewayv2_integration.management.id
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
