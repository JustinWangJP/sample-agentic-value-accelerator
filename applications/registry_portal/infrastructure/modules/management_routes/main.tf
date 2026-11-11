# 管理 handler の route のうち、このポータルが追加したものを、借用する既存 HTTP API に割り当てる。
# HTTP API・JWT authorizer・管理 Lambda とその統合（integration）は #5 の IaC（または既存環境）が持ち、
# ここでは ID と名前で受け取るだけにする。route と、その path に限った呼び出し許可だけを作る。
locals {
  # 管理 Lambda に割り当てる route と、Lambda の呼び出し許可の ID・path の末尾
  routes = {
    history = {
      route_key    = "GET /api/records/{recordId}/history"
      statement_id = "AllowHttpApiInvokeRecordHistory"
      path         = "history"
    }
    deprecation_request = {
      route_key    = "POST /api/records/{recordId}/deprecation-requests"
      statement_id = "AllowHttpApiInvokeDeprecationRequest"
      path         = "deprecation-requests"
    }
  }
}

data "aws_apigatewayv2_api" "portal" {
  api_id = var.http_api_id
}

resource "aws_apigatewayv2_route" "management" {
  for_each           = local.routes
  api_id             = var.http_api_id
  route_key          = each.value.route_key
  target             = "integrations/${var.management_integration_id}"
  authorization_type = "JWT"
  authorizer_id      = var.jwt_authorizer_id
}

# HTTP API から管理 Lambda を呼び出す許可。この module が割り当てる path だけに限る
resource "aws_lambda_permission" "management_api" {
  for_each      = local.routes
  statement_id  = each.value.statement_id
  action        = "lambda:InvokeFunction"
  function_name = var.management_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${data.aws_apigatewayv2_api.portal.execution_arn}/*/*/api/records/*/${each.value.path}"
}
