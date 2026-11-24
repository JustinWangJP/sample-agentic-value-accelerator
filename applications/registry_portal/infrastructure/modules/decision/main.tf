# 決裁 Lambda と専用の実行ロールを定義する。
# 決裁 handler（handlers/curation.py）は審査待ちの閲覧と、決裁（公開承認・却下）・公開停止・廃止（状態遷移）、
# 公開管理の照会と廃止依頼の見送り、Curator の管理レコードの検索（#34）、
# Admin による取り込み・所有変更と結果確認中の操作の回復を行う。
# descriptor の作成・更新（CreateRegistryRecord / UpdateRegistryRecord）と申請の権限は持たない
# （設計書「最小インフラとセキュリティ」の決裁 Lambda）。既存 Registry と補助テーブルは借用するだけ。
locals {
  name = var.name_prefix
}

# ロググループと、plan 時に確定するその ARN（本体スタック・notification_events と共通のモジュール）
module "log_group" {
  source            = "../lambda_log_group"
  function_name     = local.name
  retention_in_days = var.log_retention_in_days
}

# ロググループを共通のモジュールに移した。適用済みの state は moved で引き継ぐ
moved {
  from = aws_cloudwatch_log_group.decision
  to   = module.log_group.aws_cloudwatch_log_group.this
}

# 発見用 M2M client の環境変数と、顧客管理 KMS キーの場合の kms:Decrypt（本体スタックと共通）
module "discovery_client" {
  source      = "../discovery_client"
  client      = var.discovery_client
  kms_key_arn = var.discovery_kms_key_arn
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
    discovery_secret_arn = var.discovery_client.client_secret_arn
    log_group_arn        = module.log_group.arn
  })
}

# 発見用 secret が顧客管理 KMS キーで暗号化されている場合だけ、取り込みで secret を読むための
# kms:Decrypt を付ける（#49）。本体スタックの管理 Lambda と同じ modules/discovery_client の policy で、
# Secrets Manager 経由のその secret の暗号化コンテキストに限る。基本の policy（テンプレート）は条件分岐を
# 持たせないため別の資源にする
resource "aws_iam_role_policy" "decision_discovery_kms" {
  count  = var.discovery_kms_key_arn == null ? 0 : 1
  name   = "${local.name}-discovery-kms"
  role   = aws_iam_role.decision.name
  policy = module.discovery_client.kms_policy
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
  # null なら属性を設定せず、予約しない（既定の動作のまま）
  reserved_concurrent_executions = var.lambda_reserved_concurrency
  environment {
    variables = merge(module.discovery_client.environment, {
      CORS_ALLOWED_ORIGINS  = jsonencode(var.rest_api.cors_allowed_origins)
      COGNITO_ISSUER        = var.cognito_issuer
      HUMAN_CLIENT_ID       = var.human_client_id
      REGISTRY_ARN          = var.registry_arn
      SUPPLEMENT_TABLE_NAME = var.supplement_table_name
    })
  }
  depends_on = [aws_iam_role_policy.decision, module.log_group]
}

# パスと authorizer は本体スタックから借用する。
module "decision_routes" {
  source            = "../api_routes"
  route_group       = "decision"
  rest_api          = var.rest_api
  lambda_invoke_arn = aws_lambda_function.decision.invoke_arn
  function_name     = aws_lambda_function.decision.function_name
}
