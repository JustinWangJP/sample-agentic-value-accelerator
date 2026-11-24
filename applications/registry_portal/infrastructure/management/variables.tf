variable "region" {
  type        = string
  description = "管理 Lambda の実行ロールを管理する AWS リージョン。IAM はグローバルだが、プロバイダーの設定に必要"
}

variable "management_role_name" {
  type        = string
  description = "下書き API を実行する管理 Lambda の既存 IAM ロール名"
}

variable "registry_arn" {
  type        = string
  description = "変更・削除せず借用する既存 Registry の ARN。管理 Lambda の環境変数 REGISTRY_ARN と同じ値"
}

variable "supplement_table_arn" {
  type        = string
  description = "管理 Lambda の環境変数 SUPPLEMENT_TABLE_NAME が指す補助 DynamoDB テーブルの ARN"
}

variable "rest_api" {
  type = object({
    id               = string
    stage_name       = string
    authorizer_id    = string
    portal_api_scope = string
  })
  description = "管理 handler の route を割り当てる既存 REST API。本体スタックの output rest_api をそのまま転記する。stage_name は deploy.sh の再デプロイ先に使う。output の cors_allowed_origins はこのスタックでは使わないため型に含めず、転記しても Terraform が型の変換で捨てる"
}

variable "management_function_name" {
  type        = string
  description = "呼び出し許可を追加する、既存の管理 Lambda の関数名"
}
