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

variable "http_api_id" {
  type        = string
  description = "決裁の route を割り当てる既存 API Gateway HTTP API の ID"
}

variable "jwt_authorizer_id" {
  type        = string
  description = "決裁の route に適用する既存 JWT authorizer の ID"
}
