variable "function_name" {
  type        = string
  description = "ロググループを作る Lambda 関数の名前。ロググループ名は /aws/lambda/<関数名> になる"
}

variable "retention_in_days" {
  type        = number
  description = "ロググループの保持日数。既定は 365 日。CloudWatch Logs が受け付ける値だけを指定できる。各スタックの変数 log_retention_in_days の検証もここで行う"
  default     = 365
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.retention_in_days)
    error_message = "CloudWatch Logs が受け付ける保持日数（1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653）を指定してください（log_retention_in_days）。"
  }
}
