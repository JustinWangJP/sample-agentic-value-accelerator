# イベント処理 Lambda・専用の実行ロール・EventBridge ルールを定義する（#15 画面上の通知）。
# Registry が EventBridge の default バスに送るレコードの状態変化のイベントを、ルール1本でイベント処理 Lambda に流す。
# イベント処理 handler（handlers/events.py）は Registry の読み取りと、補助テーブルへの通知の書き込みだけを行う
# （設計書「最小インフラとセキュリティ」のイベント処理 Lambda）。既存 Registry と補助テーブルは借用するだけ。
locals {
  name = var.name_prefix
  # ARN の末尾（registry/<id>）の ID。イベントの detail.registryId はこの値になる
  registry_id = element(split("/", var.registry_arn), 1)
  # Registry のレコードのイベントの detail-type（README「画面上の通知」の根拠の表）
  record_detail_types = [
    "Registry Record State changed to Draft",
    "Registry Record State changed to Pending Approval",
    "Registry Record State changed to Approved",
    "Registry Record State changed to Rejected",
    "Registry Record State changed to Deprecated",
  ]
}

resource "aws_cloudwatch_log_group" "events" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 365
}

# 再試行を使い切ったイベント（元のイベントの本文）を残す。Admin が原因を取り除いた後、
# 本文をイベント処理 Lambda に非同期で送り直す（README「画面上の通知」）
resource "aws_sqs_queue" "events_dead_letter" {
  name                      = "${local.name}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_iam_role" "events" {
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

resource "aws_iam_role_policy" "events" {
  name = local.name
  role = aws_iam_role.events.name
  policy = templatefile("${path.module}/events-policy.json.tftpl", {
    registry_arn          = var.registry_arn
    supplement_table_arn  = var.supplement_table_arn
    log_group_arn         = aws_cloudwatch_log_group.events.arn
    dead_letter_queue_arn = aws_sqs_queue.events_dead_letter.arn
  })
}

resource "aws_lambda_function" "events" {
  function_name    = local.name
  role             = aws_iam_role.events.arn
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "registry_portal.handlers.events.handler"
  timeout          = 30
  memory_size      = 256
  dead_letter_config {
    target_arn = aws_sqs_queue.events_dead_letter.arn
  }
  environment {
    variables = {
      REGISTRY_ARN          = var.registry_arn
      SUPPLEMENT_TABLE_NAME = var.supplement_table_name
    }
  }
  depends_on = [aws_iam_role_policy.events, aws_cloudwatch_log_group.events]
}

# 非同期呼び出しの再試行。Registry・補助テーブルの一時的な失敗では handler が例外を返し、Lambda が再試行する。
# 重複は event ID と状態変化のキーで除くため、再試行しても通知は増えない。使い切ったイベントは DLQ に残る
resource "aws_lambda_function_event_invoke_config" "events" {
  function_name                = aws_lambda_function.events.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

resource "aws_cloudwatch_event_rule" "registry_records" {
  name           = local.name
  description    = "Registry ポータルの通知: 借用する Registry のレコードの状態変化"
  event_bus_name = "default"
  event_pattern = jsonencode({
    source        = ["aws.agent-registry"]
    "detail-type" = local.record_detail_types
    detail        = { registryId = [local.registry_id] }
  })
}

resource "aws_cloudwatch_event_target" "events" {
  rule           = aws_cloudwatch_event_rule.registry_records.name
  event_bus_name = "default"
  arn            = aws_lambda_function.events.arn
}

# イベント処理 Lambda を呼び出せるのは、このルールだけに限る
resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridgeRegistryRecords"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.events.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.registry_records.arn
}
