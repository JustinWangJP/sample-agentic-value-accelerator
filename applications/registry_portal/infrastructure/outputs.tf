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
    VITE_COGNITO_SCOPES    = "openid ${local.read_scope}"
  }
}
