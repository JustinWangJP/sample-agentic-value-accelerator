output "routes" {
  description = "作成した経路（キーは routes.json の経路名）。route_key（<メソッド> <path>）・統合先・Cognito User Pools authorizer と scope の確認に使う"
  value = {
    for name, method in aws_api_gateway_method.route : name => {
      route_key            = "${local.routes[name].method} ${local.routes[name].path}"
      target               = aws_api_gateway_integration.route[name].uri
      authorization_type   = method.authorization
      authorizer_id        = method.authorizer_id
      authorization_scopes = method.authorization_scopes
    }
  }
}

output "invoke_permissions" {
  description = "作成した Lambda の呼び出し許可（キーは routes.json の経路名）。source_arn と主体の確認に使う"
  value = {
    for name, permission in aws_lambda_permission.route : name => {
      source_arn     = permission.source_arn
      source_account = permission.source_account
      principal      = permission.principal
      function_name  = permission.function_name
    }
  }
}
