terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # state の保存先は環境ごとに `terraform init -backend-config=...` で指定する（部分設定）
  backend "s3" {}
}
