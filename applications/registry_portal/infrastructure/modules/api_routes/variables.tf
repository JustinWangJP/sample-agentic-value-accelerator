variable "rest_api_id" {
  type        = string
  description = "経路を割り当てる API Gateway REST API の ID。本体スタックが作成し、追加スタックは借用する"

  validation {
    condition     = can(regex("^[a-z0-9]{10}$", var.rest_api_id))
    error_message = "API Gateway REST API の ID（英小文字と数字の10文字）を指定してください。"
  }
}

variable "authorizer_id" {
  type        = string
  description = "経路に適用する Cognito User Pools authorizer の ID。人間用 SPA client のトークンを検証する authorizer を指定する"

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.authorizer_id))
    error_message = "REST API の Cognito User Pools authorizer の ID を指定してください。"
  }
}

variable "lambda_invoke_arn" {
  type        = string
  description = "API Gateway の AWS_PROXY 統合先の Lambda invoke ARN。"
}

variable "resource_ids" {
  type        = map(string)
  default     = null
  description = "本体スタックが作る REST API パスと resource ID の対応。追加スタックは指定せず、REST API から取得する。"
}

variable "function_name" {
  type        = string
  description = "呼び出し許可を追加する、統合先の Lambda の関数名"

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.function_name))
    error_message = "Lambda の関数名（英数字・_・- の64文字以内）を指定してください。"
  }
}

variable "route_group" {
  type        = string
  description = "routes.json から割り当てる経路のグループ。本体スタックは main、追加スタックはそれぞれ management と decision を指定する。指定できる値は routes.json のグループ名で、ここには書き写さない"

  validation {
    condition     = contains(keys(jsondecode(file("${path.module}/routes.json"))), var.route_group)
    error_message = "route_group には routes.json のグループ名（${join("・", sort(keys(jsondecode(file("${path.module}/routes.json")))))}）のいずれかを指定してください。"
  }
}

variable "authorization_scope" {
  type        = string
  description = "経路の Cognito User Pools authorizer が検証するポータル用のカスタム scope（<リソースサーバー ID>/<scope 名>）。本体スタックの local.portal_api_scope と同じ値"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.authorization_scope))
    error_message = "カスタム scope を「<リソースサーバー ID>/<scope 名>」の形で指定してください。"
  }
}
