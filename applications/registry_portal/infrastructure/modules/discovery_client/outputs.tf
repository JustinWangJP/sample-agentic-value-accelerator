output "environment" {
  description = "発見用 M2M client を使う Lambda の環境変数（DISCOVERY_*）"
  value = {
    DISCOVERY_TOKEN_URL         = var.client.token_url
    DISCOVERY_CLIENT_ID         = var.client.client_id
    DISCOVERY_CLIENT_SECRET_ARN = var.client.client_secret_arn
    DISCOVERY_SCOPE             = var.client.scope
  }
}

output "kms_policy" {
  description = "顧客管理 KMS キーの kms:Decrypt だけを許可する IAM policy（JSON）。kms_key_arn が null なら null"
  value       = local.kms_policy
}

output "kms_statements" {
  description = "kms_policy の Statement。既存の inline policy に連結する場合に使う。kms_key_arn が null なら空のリスト"
  value       = local.kms_policy == null ? [] : jsondecode(local.kms_policy).Statement
}
