variable "http_api_id" {
  type        = string
  description = "管理 handler の route を割り当てる既存 API Gateway HTTP API の ID（#5 の IaC が作成し、ここでは借用する）"

  validation {
    condition     = can(regex("^[a-z0-9]{10}$", var.http_api_id))
    error_message = "API Gateway HTTP API の ID（英小文字と数字の10文字）を指定してください。"
  }
}

variable "jwt_authorizer_id" {
  type        = string
  description = "管理 handler の route に適用する既存 JWT authorizer の ID。人間用 SPA client のトークンを検証する authorizer を指定する"

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.jwt_authorizer_id))
    error_message = "HTTP API の JWT authorizer の ID を指定してください。"
  }
}

variable "management_integration_id" {
  type        = string
  description = "既存 HTTP API にある、管理 Lambda への統合（integration）の ID。カタログや下書きの route と同じ統合を指定する"

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.management_integration_id))
    error_message = "HTTP API の統合（integration）の ID を指定してください。"
  }
}

variable "management_function_name" {
  type        = string
  description = "呼び出し許可を追加する、既存の管理 Lambda の関数名"

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.management_function_name))
    error_message = "Lambda の関数名（英数字・_・- の64文字以内）を指定してください。"
  }
}

variable "routes" {
  type = map(object({
    route_key    = string
    statement_id = string
  }))
  description = "管理 Lambda に割り当てる route（<メソッド> <path>）と、その呼び出し許可の statement ID。本体スタックと management スタックがそれぞれの route 表を渡す"

  validation {
    condition     = length(var.routes) > 0 && alltrue([for route in values(var.routes) : can(regex("^(GET|POST|PATCH|PUT|DELETE) /api/[A-Za-z0-9/{}_-]+$", route.route_key))])
    error_message = "route_key は「<メソッド> /api/<path>」の形で指定してください（ANY と $default は使えません）。"
  }
}

variable "authorization_scope" {
  type        = string
  description = "route の JWT authorizer が検証するポータル用のカスタム scope（<リソースサーバー ID>/<scope 名>）。本体スタックの output portal_api_scope と同じ値"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.authorization_scope))
    error_message = "カスタム scope を「<リソースサーバー ID>/<scope 名>」の形で指定してください。"
  }
}
