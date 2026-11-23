variable "region" {
  type        = string
  description = "イベント処理 Lambda と EventBridge ルールを作成する AWS リージョン。Registry と同じリージョン（イベントは Registry のアカウント・リージョンの default バスに届く）"
}

variable "name_prefix" {
  type        = string
  description = "イベント処理 Lambda・実行ロール・ロググループ・EventBridge ルールの名前の接頭辞"
  default     = "registry-portal-events"
}

variable "lambda_zip_path" {
  type        = string
  description = "イベント処理 handler を含むデプロイパッケージ（zip）のパス"
}

variable "registry_arn" {
  type        = string
  description = "変更・削除せず借用する既存 Registry の ARN"
}

variable "supplement_table_name" {
  type        = string
  description = "補助 DynamoDB テーブル名。イベント処理 Lambda の環境変数 SUPPLEMENT_TABLE_NAME"
}

variable "supplement_table_arn" {
  type        = string
  description = "補助 DynamoDB テーブルの ARN"
}

variable "log_retention_in_days" {
  type        = number
  description = "イベント処理 Lambda のロググループの保持日数。既定は 365 日。dev では本体スタックと同じ値（14）にする。CloudWatch Logs が受け付ける値だけを指定できる"
  default     = 365

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_in_days)
    error_message = "CloudWatch Logs が受け付ける保持日数（1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653）を指定してください。"
  }
}

variable "lambda_reserved_concurrency" {
  type        = number
  description = "イベント処理 Lambda の予約同時実行数（reserved_concurrent_executions）。null なら予約しない。EventBridge から呼ばれ、上限に達すると再試行や DLQ に回るため、本体の API より余裕のある値にする。アカウントの同時実行数の上限（aws lambda get-account-settings）を確認してから指定する"
  default     = null
}
