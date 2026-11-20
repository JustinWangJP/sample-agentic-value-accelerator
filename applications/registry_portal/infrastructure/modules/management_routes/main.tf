# 管理 handler の route のうち、このポータルが追加したものを、借用する既存 HTTP API に割り当てる。
# HTTP API・JWT authorizer・管理 Lambda とその統合（integration）は #5 の IaC（または既存環境）が持ち、
# ここでは ID と名前で受け取るだけにする。route と、その path に限った呼び出し許可だけを作る。
locals {
  # 管理 Lambda に割り当てる route と、Lambda の呼び出し許可の ID（本体スタックの local.management_routes と同じ形）
  routes = {
    admin_records = {
      route_key    = "GET /api/admin/records"
      statement_id = "AllowHttpApiInvokeAdminRecords"
    }
    admin_connection = {
      route_key    = "GET /api/admin/connection"
      statement_id = "AllowHttpApiInvokeAdminConnection"
    }
    history = {
      route_key    = "GET /api/records/{recordId}/history"
      statement_id = "AllowHttpApiInvokeRecordHistory"
    }
    deprecation_request = {
      route_key    = "POST /api/records/{recordId}/deprecation-requests"
      statement_id = "AllowHttpApiInvokeDeprecationRequest"
    }
    # 画面上の通知（#15）。自分宛ての通知と未読件数の取得と、既読位置の更新
    notifications = {
      route_key    = "GET /api/notifications"
      statement_id = "AllowHttpApiInvokeNotifications"
    }
    notifications_read = {
      route_key    = "POST /api/notifications/read"
      statement_id = "AllowHttpApiInvokeNotificationsRead"
    }
    create_record = {
      route_key    = "POST /api/records"
      statement_id = "AllowHttpApiInvokeCreateRecord"
    }
    list_records = {
      route_key    = "GET /api/records"
      statement_id = "AllowHttpApiInvokeListRecords"
    }
    get_record = {
      route_key    = "GET /api/records/{recordId}"
      statement_id = "AllowHttpApiInvokeGetRecord"
    }
    update_record = {
      route_key    = "PATCH /api/records/{recordId}"
      statement_id = "AllowHttpApiInvokeUpdateRecord"
    }
    get_operation = {
      route_key    = "GET /api/operations/{operationId}"
      statement_id = "AllowHttpApiInvokeGetOperation"
    }
    submit_record = {
      route_key    = "POST /api/records/{recordId}/submit"
      statement_id = "AllowHttpApiInvokeSubmitRecord"
    }
  }
  # 呼び出し許可の source_arn（execution ARN の stage の後ろ）は route_key から作る。
  # メソッドはそのまま、path は先頭の / を除き、{recordId} などのパスパラメータを * にする
  invoke_paths = {
    for name, route in local.routes :
    name => "${split(" ", route.route_key)[0]}/${replace(trimprefix(split(" ", route.route_key)[1], "/"), "/\\{[^}]+\\}/", "*")}"
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
  authorization_scopes = ["registry-portal/api.invoke"]
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
  source_arn    = "${data.aws_apigatewayv2_api.portal.execution_arn}/*/${local.invoke_paths[each.key]}"
}
