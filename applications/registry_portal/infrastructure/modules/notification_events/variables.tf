variable "name_prefix" {
  type        = string
  description = "イベント処理 Lambda・実行ロール・ロググループ・EventBridge ルールの名前の接頭辞（例: registry-portal-events）"
}

variable "lambda_zip_path" {
  type        = string
  description = "イベント処理 handler を含むデプロイパッケージ（zip）のパス。バックエンドと同じパッケージを使う"
}

variable "registry_arn" {
  type        = string
  description = "変更・削除せず借用する既存 Registry の ARN。イベント処理 Lambda の環境変数 REGISTRY_ARN と同じ値"

  validation {
    condition     = can(regex("^arn:aws:agent-registry:[a-z0-9-]+:[0-9]{12}:registry/[A-Za-z0-9]{12,16}$", var.registry_arn))
    error_message = "既存 Agent Registry の ARN を指定してください。"
  }
}

variable "supplement_table_name" {
  type        = string
  description = "イベント処理 Lambda の環境変数 SUPPLEMENT_TABLE_NAME が指す補助 DynamoDB テーブル名"
}

variable "supplement_table_arn" {
  type        = string
  description = "操作記録を読み、通知を書く補助 DynamoDB テーブルの ARN"

  validation {
    condition     = can(regex("^arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]+$", var.supplement_table_arn))
    error_message = "補助 DynamoDB テーブルの ARN を指定してください。"
  }
}
