variable "aws_account_id" {
  description = "反映先 AWS アカウント ID。誤ったアカウントへの反映を provider で拒否する。"
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS アカウント ID は12桁で指定してください。"
  }
}
variable "name_prefix" {
  description = "dev リソース名の接頭辞。bootstrap の permission set と揃える。"
  type        = string
  default     = "registry-portal-dev"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,39}$", var.name_prefix))
    error_message = "接頭辞は小文字英数字とハイフンの3〜40文字で指定してください。"
  }
}
variable "spa_bucket_name" {
  description = "SPA 配信用のグローバルに一意な S3 バケット名。"
  type        = string
}
variable "registry_arn" {
  description = "既存 Registry の ARN。Terraform resource として所有しない。"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:agent-registry:us-east-1:[0-9]{12}:registry/[A-Za-z0-9]{12,16}$", var.registry_arn))
    error_message = "us-east-1 の既存 Registry ARN を指定してください。"
  }
}
variable "cognito_user_pool_id" {
  description = "既存 Cognito User Pool の ID。プール本体とドメインは作成・変更しない。"
  type        = string
  validation {
    condition     = can(regex("^us-east-1_[A-Za-z0-9]+$", var.cognito_user_pool_id))
    error_message = "us-east-1 の Cognito User Pool ID を指定してください。"
  }
}
variable "cognito_domain" {
  description = "既存 Cognito managed login の HTTPS origin。末尾のスラッシュは付けない。"
  type        = string
  validation {
    condition     = can(regex("^https://[A-Za-z0-9.-]+$", var.cognito_domain))
    error_message = "Cognito ドメインは HTTPS origin で指定してください。"
  }
}
variable "identity_providers" {
  description = "既存 User Pool に設定済みの人間用 IdP 名。組織 SSO の場合は Cognito の登録名を指定する。"
  type        = list(string)
  default     = ["COGNITO"]
}
variable "team_names" {
  description = "作成する所属チーム名。team- 接頭辞は自動で付ける。既存グループは先に import する。"
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for name in var.team_names : can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", name))])
    error_message = "チーム名は英数字で開始し、英数字・ハイフン・アンダースコアで指定してください。"
  }
}
variable "discovery_token_url" {
  description = "既存の発見用 M2M client の HTTPS トークンエンドポイント。"
  type        = string
  validation {
    condition     = can(regex("^https://[A-Za-z0-9.-]+/oauth2/token$", var.discovery_token_url))
    error_message = "発見用トークン URL は HTTPS の /oauth2/token を指定してください。"
  }
}
variable "discovery_client_id" {
  description = "既存の発見用 M2M client の ID。人間用 SPA client と分離する。"
  type        = string
  validation {
    condition     = length(trimspace(var.discovery_client_id)) > 0
    error_message = "発見用 M2M client ID を指定してください。"
  }
}
variable "discovery_client_secret_arn" {
  description = "既存の発見用 M2M secret の完全な ARN。secret 値は Terraform に渡さない。"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:.+$", var.discovery_client_secret_arn))
    error_message = "発見用 M2M secret の完全な ARN を指定してください。"
  }
}
variable "discovery_kms_key_arn" {
  description = "secret に顧客管理 KMS キーを使う場合の完全なキー ARN。AWS 管理キーの場合は null。"
  type        = string
  default     = null
  validation {
    condition     = var.discovery_kms_key_arn == null ? true : can(regex("^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[A-Za-z0-9-]+$", var.discovery_kms_key_arn))
    error_message = "KMS キーの完全な ARN を指定してください。"
  }
}
variable "discovery_scope" {
  description = "既存の発見用 M2M scope。空なら既存 client の既定を使用する。"
  type        = string
  default     = ""
}
variable "lambda_zip_path" {
  description = "固定版 SDK を同梱した管理 Lambda ZIP の絶対パス。scripts/build_lambda.py で生成する。"
  type        = string
  default     = "../build/management.zip"
}
