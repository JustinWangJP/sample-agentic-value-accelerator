# 管理 Lambda の既存実行ロールに、Agent 下書きの権限を適用するルートモジュール。
# 管理 Lambda・補助テーブル・Registry は #5 の IaC（または既存環境）が持ち、ここでは借用するだけにする。
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
