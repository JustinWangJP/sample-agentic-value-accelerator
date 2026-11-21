# 管理 handler の route を HTTP API に割り当て、route ごとのメソッドと path に限った呼び出し許可を作る。
# 本体スタック（主体とカタログの route）と management スタック（このポータルが追加した route）が、それぞれの
# route 表（var.routes）を渡して使う。HTTP API・JWT authorizer・管理 Lambda とその統合（integration）は
# 呼び出し元が持ち、ここでは ID と名前で受け取るだけにする。
locals {
  # 呼び出し許可の source_arn（execution ARN の stage の後ろ）は route_key から作る。
  # メソッドはそのまま、path は先頭の / を除き、{recordId} などのパスパラメータを * にする
  invoke_paths = {
    for name, route in var.routes :
    name => "${split(" ", route.route_key)[0]}/${replace(trimprefix(split(" ", route.route_key)[1], "/"), "/\\{[^}]+\\}/", "*")}"
  }
}

data "aws_caller_identity" "current" {}

data "aws_apigatewayv2_api" "portal" {
  api_id = var.http_api_id
}

resource "aws_apigatewayv2_route" "management" {
  for_each             = var.routes
  api_id               = var.http_api_id
  route_key            = each.value.route_key
  target               = "integrations/${var.management_integration_id}"
  authorization_type   = "JWT"
  authorizer_id        = var.jwt_authorizer_id
  authorization_scopes = [var.authorization_scope]
}

# HTTP API から管理 Lambda を呼び出す許可。stage 以外は route ごとのメソッド（route_key の先頭）と path に限り、
# 同じアカウントの API Gateway に限る。
# source_arn の * は / もまたいで一致する（README「IAM と route」のリンクを参照）。そのため GET/api/records/*
# （GET /api/records/{recordId}）は、管理 Lambda の GET /api/records/{recordId}/history のほか、決裁 Lambda の
# GET /api/records/publication・/api/records/{recordId}/publication・/api/records/{recordId}/recovery の実行 ARN にも一致する。
# API Gateway が呼び出すのは route の統合先の Lambda だけなので、決裁の route から管理 Lambda が呼ばれることはない。
# 一致する他の Lambda の route は tests/test_route_permissions.py で固定し、増えたときに見直す
resource "aws_lambda_permission" "management_api" {
  for_each       = var.routes
  statement_id   = each.value.statement_id
  action         = "lambda:InvokeFunction"
  function_name  = var.management_function_name
  principal      = "apigateway.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
  source_arn     = "${data.aws_apigatewayv2_api.portal.execution_arn}/*/${local.invoke_paths[each.key]}"
}
