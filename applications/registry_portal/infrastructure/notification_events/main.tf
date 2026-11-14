# イベント処理 Lambda（画面上の通知）と EventBridge ルールを dev に適用するルートモジュール（#15）。
# 既存 Registry・補助テーブルは #5 の IaC（または既存環境）が持ち、ここでは借用するだけにする。
# CI は fmt と validate だけを行い、AWS への反映は Admin が手元で行う（ADR-0004）。
provider "aws" {
  region = var.region
}

module "notification_events" {
  source                = "../modules/notification_events"
  name_prefix           = var.name_prefix
  lambda_zip_path       = var.lambda_zip_path
  registry_arn          = var.registry_arn
  supplement_table_name = var.supplement_table_name
  supplement_table_arn  = var.supplement_table_arn
}
