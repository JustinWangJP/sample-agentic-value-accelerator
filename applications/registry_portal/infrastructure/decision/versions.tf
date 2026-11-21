terraform {
  required_version = ">= 1.10, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.71.0"
    }
  }

  # state の保存先は環境ごとに `terraform init -backend-config=backend.hcl` で指定する（部分設定）。
  # backend.hcl.example は本体と同じバケットで key を registry-portal/dev/ の下に置き、暗号化とロックを有効にする
  backend "s3" {}
}
