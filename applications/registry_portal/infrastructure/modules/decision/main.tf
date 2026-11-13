# 決裁 Lambda と専用の実行ロールを定義する。
# 決裁 handler（handlers/curation.py）は審査待ちの閲覧と、決裁（公開承認・却下）・公開停止・廃止（状態遷移）、
# 公開管理の照会と廃止依頼の見送り、
# Admin による取り込み・所有変更と結果確認中の操作の回復を行う。
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
    discovery_secret_arn = var.discovery_client_secret_arn
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
      COGNITO_ISSUER              = var.cognito_issuer
      HUMAN_CLIENT_ID             = var.human_client_id
      REGISTRY_ARN                = var.registry_arn
      SUPPLEMENT_TABLE_NAME       = var.supplement_table_name
      DISCOVERY_TOKEN_URL         = var.discovery_token_url
      DISCOVERY_CLIENT_ID         = var.discovery_client_id
      DISCOVERY_CLIENT_SECRET_ARN = var.discovery_client_secret_arn
      DISCOVERY_SCOPE             = var.discovery_scope
    }
  }
  depends_on = [aws_iam_role_policy.decision, aws_cloudwatch_log_group.decision]
}

# 決裁 handler の route を、借用する既存 HTTP API（#5 の IaC が持つ）に割り当てる。
# HTTP API と JWT authorizer は作らず ID で受け取る。route はここで定義し、決裁 Lambda だけを統合先にする
locals {
  # 決裁 Lambda を呼び出せる /api/records/{recordId} 以下の path の末尾と、そのメソッド、Lambda の呼び出し許可の ID。
  # 公開停止・廃止・公開管理の照会・廃止依頼の見送り（#13）。route と呼び出し許可の両方をこの定義から作る。
  # 回復操作の許可は下の decision_recovery_api
  decision_record_paths = {
    adopt = {
      method       = "POST"
      path         = "adopt"
      statement_id = "AllowHttpApiInvokeAdopt"
    }
    reassign_owner = {
      method       = "POST"
      path         = "reassign-owner"
      statement_id = "AllowHttpApiInvokeReassignOwner"
    }
    suspend = {
      method       = "POST"
      path         = "suspend"
      statement_id = "AllowHttpApiInvokeSuspend"
    }
    deprecate = {
      method       = "POST"
      path         = "deprecate"
      statement_id = "AllowHttpApiInvokeDeprecate"
    }
    publication = {
      method       = "GET"
      path         = "publication"
      statement_id = "AllowHttpApiInvokePublication"
    }
    decline = {
      method       = "POST"
      path         = "deprecation-requests/decline"
      statement_id = "AllowHttpApiInvokeDeclineDeprecation"
    }
  }

  routes = concat(
    [
      "GET /api/reviews",
      "GET /api/reviews/{recordId}",
      "POST /api/reviews/{recordId}/approve",
      "POST /api/reviews/{recordId}/reject",
    ],
    [for entry in values(local.decision_record_paths) : "${entry.method} /api/records/{recordId}/${entry.path}"],
    [
      "GET /api/records/{recordId}/recovery",
      "POST /api/records/{recordId}/recovery",
    ],
  )
}

data "aws_apigatewayv2_api" "portal" {
  api_id = var.http_api_id
}

resource "aws_apigatewayv2_integration" "decision" {
  api_id                 = var.http_api_id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.decision.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "decision" {
  for_each           = toset(local.routes)
  api_id             = var.http_api_id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.decision.id}"
  authorization_type = "JWT"
  authorizer_id      = var.jwt_authorizer_id
}

# HTTP API から決裁 Lambda を呼び出す許可。審査と決裁の path（/api/reviews 以下）、Curator の
# レコード単位の path（local.decision_record_paths）、回復操作の path（/api/records/{recordId}/recovery）だけに限る
resource "aws_lambda_permission" "decision_api" {
  statement_id  = "AllowHttpApiInvokeReviews"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.decision.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${data.aws_apigatewayv2_api.portal.execution_arn}/*/*/api/reviews*"
}

resource "aws_lambda_permission" "decision_curator_record_api" {
  for_each      = local.decision_record_paths
  statement_id  = each.value.statement_id
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.decision.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${data.aws_apigatewayv2_api.portal.execution_arn}/*/*/api/records/*/${each.value.path}"
}

resource "aws_lambda_permission" "decision_recovery_api" {
  statement_id  = "AllowHttpApiInvokeRecovery"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.decision.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${data.aws_apigatewayv2_api.portal.execution_arn}/*/*/api/records/*/recovery"
}
