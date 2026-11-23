variable "aws_account_id" {
  description = "反映先 AWS アカウント ID。誤ったアカウントへの反映を provider で拒否する。"
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS アカウント ID は12桁で指定してください。"
  }
}
variable "name_prefix" {
  description = "dev リソース名の接頭辞。bootstrap の permission set と揃える。"
  type        = string
  default     = "registry-portal-dev"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,39}$", var.name_prefix))
    error_message = "接頭辞は小文字英数字とハイフンの3〜40文字で指定してください。"
  }
}
variable "spa_bucket_name" {
  description = "SPA 配信用のグローバルに一意な S3 バケット名。"
  type        = string
}
variable "registry_arn" {
  description = "既存 Registry の ARN。Terraform resource として所有しない。"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:agent-registry:us-east-1:[0-9]{12}:registry/[A-Za-z0-9]{12,16}$", var.registry_arn))
    error_message = "us-east-1 の既存 Registry ARN を指定してください。"
  }
}
variable "cognito_user_pool_id" {
  description = "既存 Cognito User Pool の ID。プール本体とドメインは作成・変更しない。"
  type        = string
  validation {
    condition     = can(regex("^us-east-1_[A-Za-z0-9]+$", var.cognito_user_pool_id))
    error_message = "us-east-1 の Cognito User Pool ID を指定してください。"
  }
}
variable "cognito_domain" {
  description = "既存 Cognito managed login の HTTPS origin。末尾のスラッシュは付けない。"
  type        = string
  validation {
    condition     = can(regex("^https://[A-Za-z0-9.-]+$", var.cognito_domain))
    error_message = "Cognito ドメインは HTTPS origin で指定してください。"
  }
}
variable "identity_providers" {
  description = "既存 User Pool に設定済みの人間用 IdP 名。組織 SSO の場合は Cognito の登録名を指定する。"
  type        = list(string)
  default     = ["COGNITO"]
}
variable "team_names" {
  description = "作成する所属チーム名。team- 接頭辞は自動で付ける。既存グループは先に import する。"
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for name in var.team_names : can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", name))])
    error_message = "チーム名は英数字で開始し、英数字・ハイフン・アンダースコアで指定してください。"
  }
}
variable "discovery_token_url" {
  description = "既存の発見用 M2M client の HTTPS トークンエンドポイント（/oauth2/token）。形式は modules/discovery_client で検証する。"
  type        = string
}
variable "discovery_client_id" {
  description = "既存の発見用 M2M client の ID。人間用 SPA client と分離する。空は modules/discovery_client で拒否する。"
  type        = string
}
variable "discovery_client_secret_arn" {
  description = "既存の発見用 M2M secret の完全な ARN（末尾の 6 文字を含み、ワイルドカードを含まない）。secret 値は Terraform に渡さない。形式は modules/discovery_client で検証する。"
  type        = string
}
variable "discovery_kms_key_arn" {
  description = "secret に顧客管理 KMS キーを使う場合の完全なキー ARN。AWS 管理キーの場合は null。"
  type        = string
  default     = null
  validation {
    condition     = var.discovery_kms_key_arn == null ? true : can(regex("^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[A-Za-z0-9-]+$", var.discovery_kms_key_arn))
    error_message = "KMS キーの完全な ARN を指定してください。"
  }
}
variable "discovery_scope" {
  description = "既存の発見用 M2M scope。空なら既存 client の既定を使用する。"
  type        = string
  default     = ""
}
variable "log_retention_in_days" {
  description = "API のアクセスログと管理 Lambda のロググループの保持日数。既定は 365 日。dev では dev.tfvars で 14 にする。CloudWatch Logs が受け付ける値だけを指定できる。"
  type        = number
  default     = 365
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_in_days)
    error_message = "CloudWatch Logs が受け付ける保持日数（1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653）を指定してください。"
  }
}
variable "lambda_zip_path" {
  description = "管理・決裁・イベント処理 Lambda 共通の ZIP のパス。scripts/build_lambda.py で生成する。deploy.sh は一時ディレクトリの ZIP を指定する。"
  type        = string
  default     = "../backend/build/lambda.zip"
}
variable "table_deletion_protection" {
  description = "補助テーブルの削除保護（deletion_protection_enabled）。撤去するときだけ false で apply してから destroy する。lifecycle.prevent_destroy は変数にできないため、撤去時は手元で行を外し、その変更はコミットしない。"
  type        = bool
  default     = true
}
variable "spa_force_destroy" {
  description = "SPA バケットを中身ごと削除できるようにするか（force_destroy）。撤去するときだけ true で apply してから destroy する。"
  type        = bool
  default     = false
}
variable "spa_noncurrent_version_expiration_days" {
  description = "SPA バケットの古いバージョン（noncurrent）を消すまでの日数。null（既定）ではライフサイクル設定を作らず、古いバージョンを残す。dev の推奨値は 7。"
  type        = number
  default     = null
  validation {
    # S3 のライフサイクル規則は 1 以上の整数日だけを受け付ける。apply の前に plan で止める
    condition     = var.spa_noncurrent_version_expiration_days == null || try(var.spa_noncurrent_version_expiration_days >= 1 && floor(var.spa_noncurrent_version_expiration_days) == var.spa_noncurrent_version_expiration_days, false)
    error_message = "spa_noncurrent_version_expiration_days は null または 1 以上の整数にしてください。"
  }
}

variable "lambda_reserved_concurrency" {
  description = "管理 Lambda の予約同時実行数（reserved_concurrent_executions）。暴走や大量リクエストによる課金を抑える上限。null なら予約しない。アカウントの同時実行数の上限（aws lambda get-account-settings）を確認してから指定する。"
  type        = number
  default     = null
}

variable "local_dev_origins" {
  description = "ローカルの開発サーバー（npm run dev）から dev に接続するための origin。人間用 client のコールバック URL・ログアウト URL（末尾に / を付ける）と REST API の CORS に追加する。既定値の [] では追加しない。"
  type        = list(string)
  default     = []
  validation {
    # 開発用の HTTP コールバック先は localhost と 127.0.0.1 に限定する
    condition     = alltrue([for o in var.local_dev_origins : can(regex("^http://(localhost|127\\.0\\.0\\.1):[0-9]{1,5}$", o))])
    error_message = "local_dev_origins には http://localhost:<port> または http://127.0.0.1:<port> を、末尾の / なしで指定してください。"
  }
}

variable "allowed_source_cidrs" {
  description = "REST API が許可する接続元 IPv4 CIDR。TF_VAR_allowed_source_cidrs で渡す。"
  type        = list(string)
  nullable    = false
  validation {
    condition     = length(var.allowed_source_cidrs) > 0 && alltrue([for cidr in var.allowed_source_cidrs : can(cidrnetmask(cidr))])
    error_message = "allowed_source_cidrs に有効な IPv4 CIDR を 1 件以上指定してください。"
  }
}
