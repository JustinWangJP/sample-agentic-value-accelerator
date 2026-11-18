# 決裁 Lambda と専用の実行ロールを定義する。
# 決裁 handler（handlers/curation.py）は審査待ちの閲覧と、決裁（公開承認・却下）・公開停止・廃止（状態遷移）、
# 公開管理の照会と廃止依頼の見送り、Curator の管理レコードの検索（#34）、
# Admin による取り込み・所有変更と結果確認中の操作の回復を行う。
# descriptor の作成・更新（CreateRegistryRecord / UpdateRegistryRecord）と申請の権限は持たない
# （設計書「最小インフラとセキュリティ」の決裁 Lambda）。既存 Registry と補助テーブルは借用するだけ。
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  name           = var.name_prefix
  log_group_name = "/aws/lambda/${local.name}"
  # 作成前のロググループの arn 属性は初回の plan で未確定になり、反映前ガード（scripts/check_plan.py）が
  # 「未確定の IAM policy」で中止する。本体スタックの local.log_group_arn と同じく、名前と plan 時に確定する
  # アカウント ID・リージョンから組み立てる
  log_group_arn = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:${local.log_group_name}"
}

resource "aws_cloudwatch_log_group" "decision" {
  name              = local.log_group_name
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
    log_group_arn        = local.log_group_arn
  })
}

# 発見用 secret が顧客管理 KMS キーで暗号化されている場合だけ、取り込みで secret を読むための
# kms:Decrypt を付ける（#49）。本体スタックの管理 Lambda と同じく、Secrets Manager 経由で
# その secret の暗号化コンテキストに限る。基本の policy（テンプレート）は条件分岐を持たせないため別の資源にする
resource "aws_iam_role_policy" "decision_discovery_kms" {
  count = var.discovery_kms_key_arn == null ? 0 : 1
  name  = "${local.name}-discovery-kms"
  role  = aws_iam_role.decision.name
  policy = templatefile("${path.module}/discovery-kms-policy.json.tftpl", {
    kms_key_arn          = var.discovery_kms_key_arn
    discovery_secret_arn = var.discovery_client_secret_arn
    # secret のリージョンは本体スタックと同じく ARN から導出する
    secret_region = split(":", var.discovery_client_secret_arn)[3]
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
  # 決裁 Lambda を呼び出せる /api/records/ 以下の path と、そのメソッド、Lambda の呼び出し許可の ID。
  # Admin の取り込み・所有変更（#16）、Curator の公開停止・廃止・公開管理の照会・廃止依頼の見送り（#13）と
  # 管理レコードの検索（#34）。route と呼び出し許可の両方をこの定義から作り、呼び出し許可の source_arn では
  # {recordId} をワイルドカード（*）に置き換える。{recordId} を含まない静的な path は完全一致で許可する。
  # 回復操作（同じ path に2つのメソッド）の許可は下の decision_recovery_api
  decision_record_paths = {
    adopt = {
      method       = "POST"
      path         = "{recordId}/adopt"
      statement_id = "AllowHttpApiInvokeAdopt"
    }
    reassign_owner = {
      method       = "POST"
      path         = "{recordId}/reassign-owner"
      statement_id = "AllowHttpApiInvokeReassignOwner"
    }
    suspend = {
      method       = "POST"
      path         = "{recordId}/suspend"
      statement_id = "AllowHttpApiInvokeSuspend"
    }
    deprecate = {
      method       = "POST"
      path         = "{recordId}/deprecate"
      statement_id = "AllowHttpApiInvokeDeprecate"
    }
    publication = {
      method       = "GET"
      path         = "{recordId}/publication"
      statement_id = "AllowHttpApiInvokePublication"
    }
    # HTTP API では完全一致する静的な route が parameterized route より優先されるため
    # （README「IAM と route」のリンクを参照）、publication がレコード ID として解釈されることはない
    publication_search = {
      method       = "GET"
      path         = "publication"
      statement_id = "AllowHttpApiInvokePublicationSearch"
    }
    decline = {
      method       = "POST"
      path         = "{recordId}/deprecation-requests/decline"
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
    [for entry in values(local.decision_record_paths) : "${entry.method} /api/records/${entry.path}"],
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

# HTTP API から決裁 Lambda を呼び出す許可。審査と決裁の path（/api/reviews 以下）、Admin・Curator の
# /api/records/ 以下の path（local.decision_record_paths）、回復操作の path（/api/records/{recordId}/recovery）だけに限る
resource "aws_lambda_permission" "decision_api" {
  statement_id  = "AllowHttpApiInvokeReviews"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.decision.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${data.aws_apigatewayv2_api.portal.execution_arn}/*/*/api/reviews*"
}

# Admin の route を加えたため Curator 専用の名前から改めた。適用済みの state は moved で引き継ぐ
moved {
  from = aws_lambda_permission.decision_curator_record_api
  to   = aws_lambda_permission.decision_record_api
}

resource "aws_lambda_permission" "decision_record_api" {
  for_each      = local.decision_record_paths
  statement_id  = each.value.statement_id
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.decision.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${data.aws_apigatewayv2_api.portal.execution_arn}/*/*/api/records/${replace(each.value.path, "{recordId}", "*")}"
}

resource "aws_lambda_permission" "decision_recovery_api" {
  statement_id  = "AllowHttpApiInvokeRecovery"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.decision.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${data.aws_apigatewayv2_api.portal.execution_arn}/*/*/api/records/*/recovery"
}
