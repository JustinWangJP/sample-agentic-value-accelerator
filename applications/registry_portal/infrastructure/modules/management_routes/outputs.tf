output "routes" {
  description = "作成した route（キーは var.routes と同じ）。route_key・統合先・JWT authorizer と scope の確認に使う"
  value = {
    for name, route in aws_apigatewayv2_route.management : name => {
      route_key            = route.route_key
      target               = route.target
      authorization_type   = route.authorization_type
      authorizer_id        = route.authorizer_id
      authorization_scopes = route.authorization_scopes
    }
  }
}

output "invoke_permissions" {
  description = "作成した管理 Lambda の呼び出し許可（キーは var.routes と同じ）。source_arn と主体の確認に使う"
  value = {
    for name, permission in aws_lambda_permission.management_api : name => {
      source_arn     = permission.source_arn
      source_account = permission.source_account
      principal      = permission.principal
      function_name  = permission.function_name
    }
  }
}
