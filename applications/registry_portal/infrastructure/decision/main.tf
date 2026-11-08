# 決裁 Lambda（審査待ちの閲覧と、公開承認・却下）と、その route を dev に適用するルートモジュール。
# 既存 Registry・補助テーブル・Cognito User Pool・HTTP API と JWT authorizer は #5 の IaC（または既存環境）が持ち、
# ここでは借用するだけにする。審査と決裁の4つの route だけを、借用した HTTP API に割り当てる。
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
  http_api_id           = var.http_api_id
  jwt_authorizer_id     = var.jwt_authorizer_id
}
