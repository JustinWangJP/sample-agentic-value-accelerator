# 決裁 Lambda（審査待ちの閲覧と、公開承認・却下）と、その route を dev に適用するルートモジュール。
# 既存 Registry・補助テーブル・Cognito User Pool・REST API と Cognito User Pools authorizer は #5 の IaC（または既存環境）が持ち、
# ここでは借用するだけにする。審査・決裁・公開管理・Admin 操作の route を、借用した REST API に割り当てる。
# CI は fmt と validate だけを行い、AWS への反映は Admin が手元で行う（ADR-0004）。
provider "aws" {
  region = var.region
}

module "decision" {
  source                = "../modules/decision"
  name_prefix           = var.name_prefix
  lambda_zip_path       = var.lambda_zip_path
  cognito_issuer        = "https://cognito-idp.${var.region}.amazonaws.com/${var.cognito_user_pool_id}"
  human_client_id       = var.human_client_id
  registry_arn          = var.registry_arn
  supplement_table_name = var.supplement_table_name
  supplement_table_arn  = var.supplement_table_arn
  rest_api_id           = var.rest_api_id
  authorizer_id         = var.authorizer_id
  cors_allowed_origins  = var.cors_allowed_origins
  portal_api_scope      = var.portal_api_scope
  # 取り込みで公開承認済みの版を読む発見用 M2M（管理 Lambda と同じ client）
  discovery_client = {
    token_url         = var.discovery_token_url
    client_id         = var.discovery_client_id
    client_secret_arn = var.discovery_client_secret_arn
    scope             = var.discovery_scope
  }
  # secret が顧客管理 KMS キーで暗号化されている場合だけ指定する（本体スタックと同じ値）
  discovery_kms_key_arn = var.discovery_kms_key_arn
  log_retention_in_days = var.log_retention_in_days
  # 予約同時実行数。null なら予約しない（#61 実装案 5）
  lambda_reserved_concurrency = var.lambda_reserved_concurrency
}
