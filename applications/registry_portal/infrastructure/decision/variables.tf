variable "region" {
  type        = string
  description = "決裁 Lambda を作成する AWS リージョン"
}

variable "name_prefix" {
  type        = string
  description = "決裁 Lambda・実行ロール・ロググループの名前の接頭辞"
  default     = "registry-portal-decision"
}

variable "lambda_zip_path" {
  type        = string
  description = "決裁 handler を含むデプロイパッケージ（zip）のパス"
}

variable "cognito_user_pool_id" {
  type        = string
  description = "人間の認証に使う既存 Cognito User Pool の ID"
}

variable "human_client_id" {
  type        = string
  description = "人間用公開 SPA client の client_id"
}

variable "registry_arn" {
  type        = string
  description = "変更・削除せず借用する既存 Registry の ARN"
}

variable "supplement_table_name" {
  type        = string
  description = "補助 DynamoDB テーブル名。決裁 Lambda の環境変数 SUPPLEMENT_TABLE_NAME"
}

variable "supplement_table_arn" {
  type        = string
  description = "補助 DynamoDB テーブルの ARN"
}

variable "rest_api" {
  type = object({
    id                   = string
    stage_name           = string
    authorizer_id        = string
    portal_api_scope     = string
    cors_allowed_origins = list(string)
  })
  description = "決裁の route を割り当てる既存 REST API。本体スタックの output rest_api をそのまま転記する。stage_name は deploy.sh の再デプロイ先に、cors_allowed_origins は決裁 Lambda の CORS 許可リストに使う"
}

variable "discovery_token_url" {
  type        = string
  description = "取り込み時の公開承認済みの版を取得する発見用 M2M の HTTPS トークンエンドポイント"
}

variable "discovery_client_id" {
  type        = string
  description = "取り込みで公開承認済みの版を読む既存の発見用 M2M client ID"
}

variable "discovery_client_secret_arn" {
  type        = string
  description = "発見用 M2M client secret を保存した Secrets Manager の ARN"
}

variable "discovery_kms_key_arn" {
  type        = string
  description = "発見用 M2M secret を顧客管理 KMS キーで暗号化している場合の完全なキー ARN（本体スタックと同じ値）。AWS 管理キー（aws/secretsmanager）なら null"
  default     = null

  validation {
    condition     = var.discovery_kms_key_arn == null ? true : can(regex("^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[A-Za-z0-9-]+$", var.discovery_kms_key_arn))
    error_message = "KMS キーの完全な ARN を指定してください（エイリアスは使えません）。"
  }
}

variable "discovery_scope" {
  type        = string
  description = "発見用 M2M に要求する scope。空文字なら client の既定値"
}

variable "log_retention_in_days" {
  type        = number
  description = "決裁 Lambda のロググループの保持日数。既定は 365 日。dev では本体スタックと同じ値（14）にする。CloudWatch Logs が受け付ける値だけを指定できる"
  default     = 365
}

variable "lambda_reserved_concurrency" {
  type        = number
  description = "決裁 Lambda の予約同時実行数（reserved_concurrent_executions）。暴走や大量リクエストによる課金を抑える上限。null なら予約しない。アカウントの同時実行数の上限（aws lambda get-account-settings）を確認してから指定する"
  default     = null
}
