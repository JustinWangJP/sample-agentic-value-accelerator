# 既存の発見用 M2M client を使う Lambda（本体スタックの管理 Lambda と decision の決裁 Lambda）に共通の設定を作る。
# 資源は作らず、Lambda の環境変数と、secret が顧客管理 KMS キーで暗号化されている場合の kms:Decrypt の文を返す。
# secret ARN の検証、secret のリージョンの導出、KMS の条件をこのモジュールだけに置く。
locals {
  # secret のリージョンは ARN から導出する（Secrets Manager 経由の kms:ViaService に使う）
  secret_region = split(":", var.client.client_secret_arn)[3]
  # Secrets Manager 経由で、その secret の暗号化コンテキストに限った kms:Decrypt（#49）。
  # backend/tests/test_decision_permissions.py が文字列の置換で描画できるよう、条件分岐はテンプレートに入れない
  kms_policy = var.kms_key_arn == null ? null : templatefile("${path.module}/discovery-kms-policy.json.tftpl", {
    kms_key_arn          = var.kms_key_arn
    discovery_secret_arn = var.client.client_secret_arn
    secret_region        = local.secret_region
  })
}
