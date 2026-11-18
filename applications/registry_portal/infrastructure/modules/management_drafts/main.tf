# 管理 Lambda の既存ロールに、下書きの作成・編集・申請に必要な権限だけを追加する。
# ポリシー本文はテストで検査するため、JSON のテンプレートに分けている。
resource "aws_iam_role_policy" "management_drafts" {
  name = "registry-portal-management-drafts"
  role = var.management_role_name
  policy = templatefile("${path.module}/draft-policy.json.tftpl", {
    registry_arn         = var.registry_arn
    supplement_table_arn = var.supplement_table_arn
  })
}
