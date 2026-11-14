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
