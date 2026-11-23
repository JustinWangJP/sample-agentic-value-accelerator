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

variable "rest_api_id" {
  type        = string
  description = "管理 handler の route（履歴・廃止依頼）を割り当てる既存 API Gateway REST API の ID"
}

variable "authorizer_id" {
  type        = string
  description = "管理 handler の route に適用する既存 Cognito User Pools authorizer の ID"
}

variable "management_function_name" {
  type        = string
  description = "呼び出し許可を追加する、既存の管理 Lambda の関数名"
}

variable "portal_api_scope" {
  type        = string
  description = "route の Cognito User Pools authorizer が検証するポータル用のカスタム scope。本体スタックの output portal_api_scope を転記する"
}
