# Lambda 関数のロググループ（/aws/lambda/<関数名>、365 日保存）を作り、その ARN を返す。
# 作成前のロググループの arn 属性は初回の plan で未確定になり、反映前ガード（scripts/check_plan.py）が
# 「未確定の IAM policy」で中止する。そのため実行ロールの policy に渡す ARN は、名前と plan 時に確定する
# アカウント ID・リージョンから組み立てる。本体スタックと追加スタックの Lambda で共通に使う。
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 365
}
