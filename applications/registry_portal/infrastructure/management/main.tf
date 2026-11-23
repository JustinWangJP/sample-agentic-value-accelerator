# 管理 Lambda の既存実行ロールに Agent 下書きの権限を適用し、このポータルが追加した管理 handler の route を
# 既存 REST API に割り当てるルートモジュール。
# 管理 Lambda・REST API・補助テーブル・Registry は #5 の IaC（または既存環境）が持ち、ここでは借用するだけにする。
# CI は fmt と validate だけを行い、AWS への反映は Admin が手元で行う（ADR-0004）。
provider "aws" {
  region = var.region
}

module "management_drafts" {
  source               = "../modules/management_drafts"
  management_role_name = var.management_role_name
  registry_arn         = var.registry_arn
  supplement_table_arn = var.supplement_table_arn
}

data "aws_lambda_function" "management" {
  function_name = var.management_function_name
}

module "management_routes" {
  source              = "../modules/api_routes"
  route_group         = "management"
  authorization_scope = var.rest_api.portal_api_scope
  rest_api_id         = var.rest_api.id
  authorizer_id       = var.rest_api.authorizer_id
  lambda_invoke_arn   = data.aws_lambda_function.management.invoke_arn
  function_name       = var.management_function_name
}
