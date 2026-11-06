variable "management_role_name" {
  type        = string
  description = "下書き API を実行する管理 Lambda の既存 IAM ロール名"
}

variable "registry_arn" {
  type        = string
  description = "変更・削除せず借用する既存 Registry の ARN"

  validation {
    condition     = can(regex("^arn:aws:agent-registry:[a-z0-9-]+:[0-9]{12}:registry/[A-Za-z0-9]{12,16}$", var.registry_arn))
    error_message = "既存 Agent Registry の ARN を指定してください。"
  }
}

variable "supplement_table_arn" {
  type        = string
  description = "所有情報・ロック・冪等操作・追記記録を保存する補助 DynamoDB テーブル ARN"

  validation {
    condition     = can(regex("^arn:aws:dynamodb:[a-z0-9-]+:[0-9]{12}:table/[A-Za-z0-9_.-]+$", var.supplement_table_arn))
    error_message = "補助 DynamoDB テーブルの ARN を指定してください。"
  }
}
