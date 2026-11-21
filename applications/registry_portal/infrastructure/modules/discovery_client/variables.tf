variable "client" {
  type = object({
    token_url         = string
    client_id         = string
    client_secret_arn = string
    scope             = string
  })
  description = "既存の発見用 M2M client の接続設定。token_url は HTTPS のトークンエンドポイント、client_id は人間用 SPA client と分離した client ID、client_secret_arn は secret を保存した Secrets Manager の完全な ARN（secret 値は Terraform に渡さない）、scope は要求する scope（空文字なら client の既定値）"

  validation {
    condition     = can(regex("^https://[A-Za-z0-9.-]+/oauth2/token$", var.client.token_url))
    error_message = "発見用トークン URL は HTTPS の /oauth2/token を指定してください。"
  }

  validation {
    condition     = length(trimspace(var.client.client_id)) > 0
    error_message = "発見用 M2M client ID を指定してください。"
  }

  # IAM の Resource と kms:EncryptionContext:SecretARN の StringEquals に使うため、ワイルドカード（* と ?）を含まない
  # 完全な ARN（末尾は Secrets Manager が付ける 6 文字）に限る。
  # https://docs.aws.amazon.com/secretsmanager/latest/userguide/whats-in-a-secret.html
  validation {
    condition     = can(regex("^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$", var.client.client_secret_arn))
    error_message = "発見用 M2M secret の完全な ARN（末尾の 6 文字を含み、ワイルドカードを含まない）を指定してください。"
  }
}

variable "kms_key_arn" {
  type        = string
  description = "発見用 M2M secret を顧客管理 KMS キーで暗号化している場合の完全なキー ARN。AWS 管理キー（aws/secretsmanager）なら null。形式は呼び出し元のルート変数で検証する"
  default     = null
}
