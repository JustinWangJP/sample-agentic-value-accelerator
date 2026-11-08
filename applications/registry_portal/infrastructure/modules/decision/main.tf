# 決裁 Lambda と専用の実行ロールを定義する。
# 決裁 handler（handlers/curation.py）は審査待ちの閲覧と、公開承認・却下（状態遷移）だけを行う。
# descriptor の作成・更新（CreateRegistryRecord / UpdateRegistryRecord）と申請の権限は持たない
# （設計書「最小インフラとセキュリティ」の決裁 Lambda）。既存 Registry と補助テーブルは借用するだけ。
locals {
  name = var.name_prefix
}

resource "aws_cloudwatch_log_group" "decision" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 365
}

resource "aws_iam_role" "decision" {
  name = local.name
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "decision" {
  name = local.name
  role = aws_iam_role.decision.name
  policy = templatefile("${path.module}/decision-policy.json.tftpl", {
    registry_arn         = var.registry_arn
    supplement_table_arn = var.supplement_table_arn
    log_group_arn        = aws_cloudwatch_log_group.decision.arn
  })
}

resource "aws_lambda_function" "decision" {
  function_name    = local.name
  role             = aws_iam_role.decision.arn
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "registry_portal.handlers.curation.handler"
  timeout          = 30
  memory_size      = 256
  environment {
    variables = {
      COGNITO_ISSUER        = var.cognito_issuer
      HUMAN_CLIENT_ID       = var.human_client_id
      REGISTRY_ARN          = var.registry_arn
      SUPPLEMENT_TABLE_NAME = var.supplement_table_name
    }
  }
  depends_on = [aws_iam_role_policy.decision, aws_cloudwatch_log_group.decision]
}
