# 管理 handler の route のうち、このポータルが追加したものを、借用する既存 HTTP API に割り当てる。
# HTTP API・JWT authorizer・管理 Lambda とその統合（integration）は #5 の IaC（または既存環境）が持ち、
# ここでは ID と名前で受け取るだけにする。route と、その path に限った呼び出し許可だけを作る。
locals {
  # 管理 Lambda に割り当てる route と、Lambda の呼び出し許可の ID・path（execution ARN の後ろ）
  routes = {
    admin_records = {
      route_key    = "GET /api/admin/records"
      statement_id = "AllowHttpApiInvokeAdminRecords"
      path         = "api/admin/records"
    }
    admin_connection = {
      route_key    = "GET /api/admin/connection"
      statement_id = "AllowHttpApiInvokeAdminConnection"
      path         = "api/admin/connection"
    }
    history = {
      route_key    = "GET /api/records/{recordId}/history"
      statement_id = "AllowHttpApiInvokeRecordHistory"
      path         = "api/records/*/history"
    }
    deprecation_request = {
      route_key    = "POST /api/records/{recordId}/deprecation-requests"
      statement_id = "AllowHttpApiInvokeDeprecationRequest"
      path         = "api/records/*/deprecation-requests"
    }
    # 画面上の通知（#15）。自分宛ての通知と未読件数の取得と、既読位置の更新
    notifications = {
      route_key    = "GET /api/notifications"
      statement_id = "AllowHttpApiInvokeNotifications"
      path         = "api/notifications"
    }
    notifications_read = {
      route_key    = "POST /api/notifications/read"
      statement_id = "AllowHttpApiInvokeNotificationsRead"
      path         = "api/notifications/read"
    }
    create_record = {
      route_key    = "POST /api/records"
      statement_id = "AllowHttpApiInvokeCreateRecord"
      path         = "api/records"
    }
    list_records = {
      route_key    = "GET /api/records"
      statement_id = "AllowHttpApiInvokeListRecords"
      path         = "api/records"
    }
    get_record = {
      route_key    = "GET /api/records/{recordId}"
      statement_id = "AllowHttpApiInvokeGetRecord"
      path         = "api/records/*"
    }
    update_record = {
      route_key    = "PATCH /api/records/{recordId}"
      statement_id = "AllowHttpApiInvokeUpdateRecord"
      path         = "api/records/*"
    }
    get_operation = {
      route_key    = "GET /api/operations/{operationId}"
      statement_id = "AllowHttpApiInvokeGetOperation"
      path         = "api/operations/*"
    }
    submit_record = {
      route_key    = "POST /api/records/{recordId}/submit"
      statement_id = "AllowHttpApiInvokeSubmitRecord"
      path         = "api/records/*/submit"
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
  # 本体スタックのリソースサーバーが定義し、人間用 SPA client に許可したポータル用の scope
  authorization_scopes = ["registry-portal/catalog.read"]
}

# HTTP API から管理 Lambda を呼び出す許可。本体スタックの aws_lambda_permission.api と同じく、
# stage 以外は route ごとのメソッド（route_key の先頭）と path に限る。
# source_arn の * は / もまたぐため、api/records/* は GET の履歴（api/records/*/history）にも一致する。
# 同じメソッドの route はすべて管理 Lambda に向くので、許可の範囲は広がらない
resource "aws_lambda_permission" "management_api" {
  for_each      = local.routes
  statement_id  = each.value.statement_id
  action        = "lambda:InvokeFunction"
  function_name = var.management_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${data.aws_apigatewayv2_api.portal.execution_arn}/*/${split(" ", each.value.route_key)[0]}/${each.value.path}"
}
