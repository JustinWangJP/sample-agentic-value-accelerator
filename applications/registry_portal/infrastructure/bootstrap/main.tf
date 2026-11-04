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
variable "identity_center_instance_arn" {
  description = "既存 IAM Identity Center instance の ARN。"
  type        = string
}
variable "deployer_group_id" {
  description = "RegistryPortalDeployer を割り当てる既存 Identity Center グループ ID。"
  type        = string
}

locals {
  state_arn = "arn:aws:s3:::${var.state_bucket_name}"
  spa_arn   = "arn:aws:s3:::${var.spa_bucket_name}"
  sso_role  = "arn:aws:iam::${var.aws_account_id}:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_RegistryPortalDeployer_*"
  role_arn  = "arn:aws:iam::${var.aws_account_id}:role/${var.name_prefix}-management"
  pool_arn  = "arn:aws:cognito-idp:us-east-1:${var.aws_account_id}:userpool/${var.cognito_user_pool_id}"
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
        Action    = ["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*", "s3:ListBucket*"]
        Resource  = [local.state_arn, "${local.state_arn}/*"]
        Condition = { ArnNotLike = { "aws:PrincipalArn" = local.sso_role } }
      }
    ]
  })
  depends_on = [aws_s3_bucket_public_access_block.state]
}
resource "aws_ssoadmin_permission_set" "deployer" {
  name             = "RegistryPortalDeployer"
  description      = "Registry ポータル dev のローカル手動配信用。Registry 自体には書き込めない。"
  instance_arn     = var.identity_center_instance_arn
  session_duration = "PT1H"
}
resource "aws_ssoadmin_permission_set_inline_policy" "deployer" {
  instance_arn       = var.identity_center_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.deployer.arn
  inline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyRegistryMutation"
        Effect = "Deny"
        Action = [
          "agent-registry:CreateRegistry", "agent-registry:UpdateRegistry", "agent-registry:DeleteRegistry",
          "agent-registry:CreateRegistryRecord", "agent-registry:UpdateRegistryRecord",
          "agent-registry:UpdateRegistryRecordStatus", "agent-registry:DeleteRegistryRecord",
          "bedrock-agentcore:CreateRegistry*", "bedrock-agentcore:UpdateRegistry*", "bedrock-agentcore:DeleteRegistry*"
        ]
        Resource = "*"
      },
      {
        Sid       = "StateList"
        Effect    = "Allow"
        Action    = ["s3:ListBucket", "s3:ListBucketVersions"]
        Resource  = local.state_arn
        Condition = { StringLike = { "s3:prefix" = ["registry-portal/dev/*"] } }
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
        Resource = ["${local.state_arn}/registry-portal/dev/terraform.tfstate", "${local.state_arn}/registry-portal/dev/terraform.tfstate.tflock"]
      },
      {
        Sid      = "ReleaseLock"
        Effect   = "Allow"
        Action   = "s3:DeleteObject"
        Resource = "${local.state_arn}/registry-portal/dev/terraform.tfstate.tflock"
      },
      {
        Sid      = "SpaBucket"
        Effect   = "Allow"
        Action   = ["s3:CreateBucket", "s3:GetBucket*", "s3:ListBucket", "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration", "s3:GetAccelerateConfiguration", "s3:GetReplicationConfiguration", "s3:PutBucket*", "s3:PutEncryptionConfiguration", "s3:DeleteBucketPolicy"]
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
        Resource = local.role_arn
      },
      {
        Sid       = "PassOnlyManagementRoleToLambda"
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = local.role_arn
        Condition = { StringEquals = { "iam:PassedToService" = "lambda.amazonaws.com" } }
      },
      {
        Sid      = "ManagementFunction"
        Effect   = "Allow"
        Action   = ["lambda:CreateFunction", "lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:GetPolicy", "lambda:ListVersionsByFunction", "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration", "lambda:AddPermission", "lambda:RemovePermission", "lambda:DeleteFunction", "lambda:ListTags", "lambda:TagResource", "lambda:UntagResource", "lambda:GetFunctionCodeSigningConfig", "lambda:GetRuntimeManagementConfig"]
        Resource = "arn:aws:lambda:us-east-1:${var.aws_account_id}:function:${var.name_prefix}-management"
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
        Resource = "arn:aws:dynamodb:us-east-1:${var.aws_account_id}:table/${var.name_prefix}-auxiliary"
      },
      {
        Sid      = "PortalLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy", "logs:DeleteRetentionPolicy", "logs:ListTagsForResource", "logs:TagResource", "logs:UntagResource"]
        Resource = ["arn:aws:logs:us-east-1:${var.aws_account_id}:log-group:/aws/lambda/${var.name_prefix}-management*", "arn:aws:logs:us-east-1:${var.aws_account_id}:log-group:/aws/apigateway/${var.name_prefix}*"]
      },
      {
        Sid      = "DescribeLogs"
        Effect   = "Allow"
        Action   = "logs:DescribeLogGroups"
        Resource = "*"
      },
      {
        Sid      = "HttpApi"
        Effect   = "Allow"
        Action   = ["apigateway:GET", "apigateway:POST", "apigateway:PATCH", "apigateway:PUT", "apigateway:DELETE"]
        Resource = ["arn:aws:apigateway:us-east-1::/apis", "arn:aws:apigateway:us-east-1::/apis/*", "arn:aws:apigateway:us-east-1::/tags/*"]
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
  instance_arn       = var.identity_center_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.deployer.arn
  principal_id       = var.deployer_group_id
  principal_type     = "GROUP"
  target_id          = var.aws_account_id
  target_type        = "AWS_ACCOUNT"
  depends_on         = [aws_ssoadmin_permission_set_inline_policy.deployer]
}
output "state_bucket_name" {
  description = "本体の backend.hcl に設定するバケット名。"
  value       = aws_s3_bucket.state.id
}
