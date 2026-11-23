variable "function_name" {
  type        = string
  description = "ロググループを作る Lambda 関数の名前。ロググループ名は /aws/lambda/<関数名> になる"
}

variable "retention_in_days" {
  type        = number
  description = "ロググループの保持日数。既定は 365 日。許容値の検証は呼び出し元のスタックの変数 log_retention_in_days で行う"
  default     = 365
}
