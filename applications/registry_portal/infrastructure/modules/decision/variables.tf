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

variable "rest_api" {
  type = object({
    id                   = string
    stage_name           = string
    authorizer_id        = string
    portal_api_scope     = string
    cors_allowed_origins = list(string)
  })
  description = "決裁の route を割り当てる既存 REST API（本体スタックの output rest_api と同じ形）。ID・authorizer の ID・scope の形式は modules/api_routes で検証する。cors_allowed_origins は決裁 Lambda の CORS 許可リスト"
}

variable "discovery_client" {
  type = object({
    token_url         = string
    client_id         = string
    client_secret_arn = string
    scope             = string
  })
  description = "取り込みで公開承認済みの版を読む既存の発見用 M2M client の接続設定（HTTPS のトークンエンドポイント、client ID、client secret を保存した Secrets Manager の完全な ARN、要求する scope。scope が空文字なら client の既定値）。形式は modules/discovery_client で検証する"
}

variable "discovery_kms_key_arn" {
  type        = string
  description = "発見用 M2M secret を顧客管理 KMS キーで暗号化している場合の完全なキー ARN。指定すると決裁 Lambda に条件付きの kms:Decrypt を付ける。AWS 管理キー（aws/secretsmanager）なら null"
  default     = null

  validation {
    condition     = var.discovery_kms_key_arn == null ? true : can(regex("^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[A-Za-z0-9-]+$", var.discovery_kms_key_arn))
    error_message = "KMS キーの完全な ARN を指定してください（エイリアスは使えません）。"
  }
}

variable "log_retention_in_days" {
  type        = number
  description = "決裁 Lambda のロググループの保持日数。許容値の検証はルートモジュールの変数で行う"
  default     = 365
}

variable "lambda_reserved_concurrency" {
  type        = number
  description = "決裁 Lambda の予約同時実行数（reserved_concurrent_executions）。null なら予約しない"
  default     = null
}
