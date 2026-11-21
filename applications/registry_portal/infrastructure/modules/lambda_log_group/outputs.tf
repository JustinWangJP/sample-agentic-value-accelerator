output "name" {
  description = "ロググループ名（/aws/lambda/<関数名>）"
  value       = aws_cloudwatch_log_group.this.name
}

output "arn" {
  description = "plan 時に確定するロググループの ARN（末尾の :* を含まない）。実行ロールの policy に使う"
  value       = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.function_name}"
}
