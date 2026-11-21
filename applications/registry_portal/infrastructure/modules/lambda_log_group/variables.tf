variable "function_name" {
  type        = string
  description = "ロググループを作る Lambda 関数の名前。ロググループ名は /aws/lambda/<関数名> になる"
}
