terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.71.0"
    }
  }
}
provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = [var.aws_account_id]
}
variable "aws_account_id" {
  description = "dev 環境の AWS アカウント ID。"
  type        = string
}
variable "state_bucket_name" {
  description = "state 用 S3 バケットのグローバルに一意な名前。"
  type        = string
}
variable "bootstrap_admin_role_arn" {
  description = "bootstrap を管理する既存 IAM ロール ARN。state の内容にはアクセスできず、バケット一覧の拒否だけを免除する。STS セッション ARN ではなく IAM ロール ARN を指定する。"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:iam::${var.aws_account_id}:role/[^*?]+$", var.bootstrap_admin_role_arn))
    error_message = "bootstrap 管理者には同一アカウントの具体的な IAM ロール ARN を指定してください。"
  }
}
variable "spa_bucket_name" {
  description = "本体スタックの SPA バケット名。"
  type        = string
}
variable "name_prefix" {
  description = "本体スタックと一致するリソース名の接頭辞。"
  type        = string
  default     = "registry-portal-dev"
}

variable "cognito_user_pool_id" {
  description = "既存 Cognito User Pool ID。子 client とグループだけを管理する。"
  type        = string
}
variable "deployer_principal_arn" {
  description = "state とロックを読み書きできる唯一の主体の IAM ロール ARN（バケットポリシーの aws:PrincipalArn と照合する）。個人アカウントでは IAM ロール RegistryPortalDeployer の ARN、Identity Center では arn:aws:iam::<アカウント ID>:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_RegistryPortalDeployer_* を指定する。STS セッション ARN は指定しない。"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:iam::${var.aws_account_id}:role/", var.deployer_principal_arn)) && strcontains(var.deployer_principal_arn, "RegistryPortalDeployer")
    error_message = "deployer には同一アカウントの RegistryPortalDeployer の IAM ロール ARN を指定してください。"
  }
}
variable "identity_center_instance_arn" {
  description = "既存 IAM Identity Center instance の ARN。null（既定）なら permission set と割り当てを作らない（Identity Center のない個人アカウント向け）。"
  type        = string
  default     = null
}
variable "deployer_group_id" {
  description = "RegistryPortalDeployer を割り当てる既存 Identity Center グループ ID。identity_center_instance_arn を指定したときだけ必要。"
  type        = string
  default     = null
  validation {
    condition     = (var.identity_center_instance_arn == null) == (var.deployer_group_id == null)
    error_message = "identity_center_instance_arn と deployer_group_id は両方指定するか、両方とも省略してください。"
  }
}
variable "budget_limit_usd" {
  description = "月額の予算アラートの上限（USD）。null（既定）なら予算を作らない。dev の推奨値は 5。budget_notification_emails と両方を指定したときだけ予算を作る。"
  type        = number
  default     = null
}
variable "budget_notification_emails" {
  description = "予算アラートの通知先メールアドレス。空（既定）なら予算を作らない。個人情報なので tfvars にはコミットせず、環境変数 TF_VAR_budget_notification_emails で渡す。"
  type        = list(string)
  default     = []
}
variable "manage_api_gateway_account" {
  description = "このリージョンの API Gateway の CloudWatch ロール設定を bootstrap で管理する。既存の設定を使う場合は false にする。"
  type        = bool
  default     = false
}

data "aws_region" "current" {}

locals {
  region              = data.aws_region.current.region
  state_arn           = "arn:aws:s3:::${var.state_bucket_name}"
  spa_arn             = "arn:aws:s3:::${var.spa_bucket_name}"
  pool_arn            = "arn:aws:cognito-idp:${local.region}:${var.aws_account_id}:userpool/${var.cognito_user_pool_id}"
  use_identity_center = var.identity_center_instance_arn != null
  # Registry の書き込み action。反映前ガード（scripts/check_plan.py）と同じファイルを読み、一覧を 1 か所に保つ
  registry_write = jsondecode(file("${path.module}/../../scripts/registry_write_actions.json"))
  registry_write_actions = [
    for pair in setproduct(local.registry_write.service_prefixes, local.registry_write.actions) :
    "${pair[0]}:${pair[1]}"
  ]
  # 本体スタック（infrastructure/main.tf）の state key と資源名。bootstrap は本体より先に適用するため、
  # 本体の資源を参照できず名前から組み立てる。本体と同じ規則にし、tests/test_bootstrap_names.py で照合する
  main_stack = {
    state_key       = "registry-portal/dev/terraform.tfstate"
    management_name = "${var.name_prefix}-management"
    auxiliary_table = "${var.name_prefix}-auxiliary"
    api_log_group   = "/aws/apigateway/${var.name_prefix}"
  }
  main_state_arn = "${local.state_arn}/${local.main_stack.state_key}"
  # 管理 Lambda の実行ロール（PassRole の対象）・関数・補助テーブル・ロググループ
  management_role_arn     = "arn:aws:iam::${var.aws_account_id}:role/${local.main_stack.management_name}"
  management_function_arn = "arn:aws:lambda:${local.region}:${var.aws_account_id}:function:${local.main_stack.management_name}"
  auxiliary_table_arn     = "arn:aws:dynamodb:${local.region}:${var.aws_account_id}:table/${local.main_stack.auxiliary_table}"
  portal_log_group_arns = [
    "arn:aws:logs:${local.region}:${var.aws_account_id}:log-group:/aws/lambda/${local.main_stack.management_name}*",
    "arn:aws:logs:${local.region}:${var.aws_account_id}:log-group:${local.main_stack.api_log_group}*",
  ]
}
resource "aws_s3_bucket" "state" {
  bucket        = var.state_bucket_name
  force_destroy = false
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}
# state の更新のたびに残る古いバージョンを期限で消す。古い state に戻す場合に備えて日数は長めにし、
# 直近のバージョンは日数にかかわらず残す。現行の state は失効させない。
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-noncurrent-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days           = 90
      newer_noncurrent_versions = 10
    }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  # バージョニングが有効になってからライフサイクル設定を適用する
  depends_on = [aws_s3_bucket_versioning.state]
}
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "RequireTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [local.state_arn, "${local.state_arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "StateOnlyForDeployer"
        Effect    = "Deny"
        Principal = "*"
        Action    = ["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*"]
        Resource  = "${local.state_arn}/*"
        Condition = { ArnNotLike = { "aws:PrincipalArn" = var.deployer_principal_arn } }
      },
      {
        Sid       = "StateListOnlyForOperators"
        Effect    = "Deny"
        Principal = "*"
        Action    = ["s3:ListBucket*"]
        Resource  = local.state_arn
        Condition = { ArnNotLike = { "aws:PrincipalArn" = [var.deployer_principal_arn, var.bootstrap_admin_role_arn] } }
      }
    ]
  })
  depends_on = [aws_s3_bucket_public_access_block.state]
}
# 以下の Identity Center の資源は identity_center_instance_arn を指定したときだけ作る。
# 個人アカウントでは IAM ロール RegistryPortalDeployer（MFA 付き AssumeRole）を手動で用意する。
resource "aws_ssoadmin_permission_set" "deployer" {
  count            = local.use_identity_center ? 1 : 0
  name             = "RegistryPortalDeployer"
  description      = "Registry ポータル dev のローカル手動配信用。Registry 自体には書き込めない。"
  instance_arn     = var.identity_center_instance_arn
  session_duration = "PT1H"
}
resource "aws_ssoadmin_permission_set_inline_policy" "deployer" {
  count              = local.use_identity_center ? 1 : 0
  instance_arn       = var.identity_center_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.deployer[0].arn
  inline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyRegistryMutation"
        Effect = "Deny"
        # 反映前ガード（scripts/check_plan.py）と同じ一覧から、両方の prefix の action を作る
        Action   = local.registry_write_actions
        Resource = "*"
      },
      {
        Sid       = "StateList"
        Effect    = "Allow"
        Action    = ["s3:ListBucket", "s3:ListBucketVersions"]
        Resource  = local.state_arn
        Condition = { StringLike = { "s3:prefix" = ["${dirname(local.main_stack.state_key)}/*"] } }
      },
      {
        Sid      = "StateBucketLocation"
        Effect   = "Allow"
        Action   = "s3:GetBucketLocation"
        Resource = local.state_arn
      },
      {
        Sid      = "StateAndLock"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"]
        Resource = [local.main_state_arn, "${local.main_state_arn}.tflock"]
      },
      {
        Sid      = "ReleaseLock"
        Effect   = "Allow"
        Action   = "s3:DeleteObject"
        Resource = "${local.main_state_arn}.tflock"
      },
      {
        Sid      = "SpaBucket"
        Effect   = "Allow"
        Action   = ["s3:CreateBucket", "s3:GetBucket*", "s3:ListBucket", "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration", "s3:GetAccelerateConfiguration", "s3:GetReplicationConfiguration", "s3:PutBucket*", "s3:PutEncryptionConfiguration", "s3:PutLifecycleConfiguration", "s3:DeleteBucketPolicy"]
        Resource = local.spa_arn
      },
      {
        Sid      = "SpaObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${local.spa_arn}/*"
      },
      {
        Sid      = "ManagementRole"
        Effect   = "Allow"
        Action   = ["iam:CreateRole", "iam:GetRole", "iam:DeleteRole", "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:TagRole", "iam:UntagRole"]
        Resource = local.management_role_arn
      },
      {
        Sid       = "PassOnlyManagementRoleToLambda"
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = local.management_role_arn
        Condition = { StringEquals = { "iam:PassedToService" = "lambda.amazonaws.com" } }
      },
      {
        Sid      = "ManagementFunction"
        Effect   = "Allow"
        Action   = ["lambda:CreateFunction", "lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:GetPolicy", "lambda:ListVersionsByFunction", "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration", "lambda:AddPermission", "lambda:RemovePermission", "lambda:DeleteFunction", "lambda:ListTags", "lambda:TagResource", "lambda:UntagResource", "lambda:GetFunctionCodeSigningConfig", "lambda:GetRuntimeManagementConfig"]
        Resource = local.management_function_arn
      },
      {
        Sid      = "ExistingPoolChildren"
        Effect   = "Allow"
        Action   = ["cognito-idp:CreateUserPoolClient", "cognito-idp:DescribeUserPoolClient", "cognito-idp:UpdateUserPoolClient", "cognito-idp:DeleteUserPoolClient", "cognito-idp:CreateResourceServer", "cognito-idp:DescribeResourceServer", "cognito-idp:UpdateResourceServer", "cognito-idp:DeleteResourceServer", "cognito-idp:CreateGroup", "cognito-idp:GetGroup", "cognito-idp:UpdateGroup", "cognito-idp:DeleteGroup"]
        Resource = local.pool_arn
      },
      {
        Sid      = "AuxiliaryTable"
        Effect   = "Allow"
        Action   = ["dynamodb:CreateTable", "dynamodb:DescribeTable", "dynamodb:UpdateTable", "dynamodb:DescribeContinuousBackups", "dynamodb:UpdateContinuousBackups", "dynamodb:DescribeTimeToLive", "dynamodb:UpdateTimeToLive", "dynamodb:ListTagsOfResource", "dynamodb:TagResource", "dynamodb:UntagResource"]
        Resource = local.auxiliary_table_arn
      },
      {
        Sid      = "PortalLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy", "logs:DeleteRetentionPolicy", "logs:ListTagsForResource", "logs:TagResource", "logs:UntagResource"]
        Resource = local.portal_log_group_arns
      },
      {
        Sid      = "DescribeLogs"
        Effect   = "Allow"
        Action   = "logs:DescribeLogGroups"
        Resource = "*"
      },
      {
        Sid    = "PortalApis"
        Effect = "Allow"
        Action = ["apigateway:GET", "apigateway:POST", "apigateway:PATCH", "apigateway:PUT", "apigateway:DELETE"]
        # 移行で既存 HTTP API を削除するため、/apis への権限も必要。
        Resource = [
          "arn:aws:apigateway:${local.region}::/apis", "arn:aws:apigateway:${local.region}::/apis/*",
          "arn:aws:apigateway:${local.region}::/restapis", "arn:aws:apigateway:${local.region}::/restapis/*",
          "arn:aws:apigateway:${local.region}::/tags/*",
        ]
      },
      {
        Sid      = "SpaDistribution"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateDistribution", "cloudfront:CreateDistributionWithTags", "cloudfront:GetDistribution", "cloudfront:GetDistributionConfig", "cloudfront:UpdateDistribution", "cloudfront:DeleteDistribution", "cloudfront:CreateOriginAccessControl", "cloudfront:GetOriginAccessControl", "cloudfront:GetOriginAccessControlConfig", "cloudfront:UpdateOriginAccessControl", "cloudfront:DeleteOriginAccessControl", "cloudfront:CreateResponseHeadersPolicy", "cloudfront:GetResponseHeadersPolicy", "cloudfront:GetResponseHeadersPolicyConfig", "cloudfront:UpdateResponseHeadersPolicy", "cloudfront:DeleteResponseHeadersPolicy", "cloudfront:CreateFunction", "cloudfront:DescribeFunction", "cloudfront:GetFunction", "cloudfront:UpdateFunction", "cloudfront:PublishFunction", "cloudfront:DeleteFunction", "cloudfront:ListTagsForResource", "cloudfront:TagResource", "cloudfront:UntagResource", "cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
        Resource = "*"
      }
    ]
  })
}
resource "aws_ssoadmin_account_assignment" "deployer" {
  count              = local.use_identity_center ? 1 : 0
  instance_arn       = var.identity_center_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.deployer[0].arn
  principal_id       = var.deployer_group_id
  principal_type     = "GROUP"
  target_id          = var.aws_account_id
  target_type        = "AWS_ACCOUNT"
  depends_on         = [aws_ssoadmin_permission_set_inline_policy.deployer]
}
# 想定外の課金に早く気づくための月額の予算アラート。ポータルを撤去しても残るよう、撤去の対象外である
# bootstrap に置く（ADR-0014）。上限と通知先の両方を指定したときだけ作る
resource "aws_budgets_budget" "monthly" {
  count        = var.budget_limit_usd != null && length(var.budget_notification_emails) > 0 ? 1 : 0
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  # 実績が上限の 80% を超えたとき
  notification {
    notification_type          = "ACTUAL"
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = var.budget_notification_emails
  }
  # 月末の予測が上限を超えたとき
  notification {
    notification_type          = "FORECASTED"
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = var.budget_notification_emails
  }
}
# API Gateway の CloudWatch ロール設定（REST API のアクセスログに必要。リージョン内で共有）
data "aws_partition" "current" {}

resource "aws_iam_role" "api_gateway_logs" {
  count = var.manage_api_gateway_account ? 1 : 0
  name  = "${var.name_prefix}-api-gateway-logs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "api_gateway_logs" {
  count      = length(aws_iam_role.api_gateway_logs)
  role       = aws_iam_role.api_gateway_logs[count.index].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_api_gateway_account" "portal" {
  count               = length(aws_iam_role.api_gateway_logs)
  cloudwatch_role_arn = aws_iam_role.api_gateway_logs[count.index].arn
  depends_on          = [aws_iam_role_policy_attachment.api_gateway_logs]
}

output "state_bucket_name" {
  description = "本体の backend.hcl に設定するバケット名。"
  value       = aws_s3_bucket.state.id
}
