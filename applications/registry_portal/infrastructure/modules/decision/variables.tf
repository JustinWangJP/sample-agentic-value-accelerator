variable "name_prefix" {
  type        = string
  description = "決裁 Lambda・実行ロール・ロググループの名前の接頭辞（例: registry-portal-decision）"
}

variable "lambda_zip_path" {
  type        = string
  description = "決裁 handler を含むデプロイパッケージ（zip）のパス。バックエンドと同じパッケージを使う"
}

variable "cognito_issuer" {
  type        = string
  description = "人間用アクセストークンの issuer（https://cognito-idp.<region>.amazonaws.com/<pool-id>）"

  validation {
    condition     = can(regex("^https://cognito-idp\\.[a-z0-9-]+\\.amazonaws\\.com/[A-Za-z0-9_-]+$", var.cognito_issuer))
    error_message = "Cognito User Pool の issuer を指定してください。"
  }
}

variable "human_client_id" {
  type        = string
  description = "人間用公開 SPA client の client_id。決裁 handler はこの client のトークンだけを受け付ける"
}

variable "registry_arn" {
  type        = string
  description = "変更・削除せず借用する既存 Registry の ARN。決裁 Lambda の環境変数 REGISTRY_ARN と同じ値"

  validation {
    condition     = can(regex("^arn:aws:agent-registry:[a-z0-9-]+:[0-9]{12}:registry/[A-Za-z0-9]{12,16}$", var.registry_arn))
    error_message = "既存 Agent Registry の ARN を指定してください。"
  }
}

variable "supplement_table_name" {
  type        = string
  description = "決裁 Lambda の環境変数 SUPPLEMENT_TABLE_NAME が指す補助 DynamoDB テーブル名"
}

variable "supplement_table_arn" {
  type        = string
  description = "申請スナップショット・管理情報・操作記録を保存する補助 DynamoDB テーブル ARN"

  validation {
    condition     = can(regex("^arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]+$", var.supplement_table_arn))
    error_message = "補助 DynamoDB テーブルの ARN を指定してください。"
  }
}

variable "http_api_id" {
  type        = string
  description = "決裁の route を割り当てる既存 API Gateway HTTP API の ID（#5 の IaC が作成し、ここでは借用する）"

  validation {
    condition     = can(regex("^[a-z0-9]{10}$", var.http_api_id))
    error_message = "API Gateway HTTP API の ID（英小文字と数字の10文字）を指定してください。"
  }
}

variable "jwt_authorizer_id" {
  type        = string
  description = "決裁の route に適用する既存 JWT authorizer の ID。人間用 SPA client のトークンを検証する authorizer を指定する"

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.jwt_authorizer_id))
    error_message = "HTTP API の JWT authorizer の ID を指定してください。"
  }
}
