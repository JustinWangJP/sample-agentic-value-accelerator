variable "function_name" {
  type        = string
  description = "ロググループを作る Lambda 関数の名前。ロググループ名は /aws/lambda/<関数名> になる"
}

variable "retention_in_days" {
  type        = number
  description = "ロググループの保持日数。既定は 365 日。CloudWatch Logs が受け付ける値（retention_days.json）だけを指定できる。各スタックの変数 log_retention_in_days も同じ一覧で検証する"
  default     = 365
  validation {
    condition     = contains(jsondecode(file("${path.module}/retention_days.json")), var.retention_in_days)
    error_message = "CloudWatch Logs が受け付ける保持日数（${join(", ", jsondecode(file("${path.module}/retention_days.json")))}）を指定してください（log_retention_in_days）。"
  }
}
