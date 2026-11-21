# 管理 Lambda の既存実行ロールに Agent 下書きの権限を適用し、このポータルが追加した管理 handler の route を
# 既存 HTTP API に割り当てるルートモジュール。
# 管理 Lambda・HTTP API・補助テーブル・Registry は #5 の IaC（または既存環境）が持ち、ここでは借用するだけにする。
# CI は fmt と validate だけを行い、AWS への反映は Admin が手元で行う（ADR-0004）。
provider "aws" {
  region = var.region
}

module "management_drafts" {
  source               = "../modules/management_drafts"
  management_role_name = var.management_role_name
  registry_arn         = var.registry_arn
  supplement_table_arn = var.supplement_table_arn
}

# このポータルが管理 handler に追加した route と、Lambda の呼び出し許可の ID（本体スタックの local.management_routes と同じ形）
locals {
  management_routes = {
    admin_records = {
      route_key    = "GET /api/admin/records"
      statement_id = "AllowHttpApiInvokeAdminRecords"
    }
    admin_connection = {
      route_key    = "GET /api/admin/connection"
      statement_id = "AllowHttpApiInvokeAdminConnection"
    }
    history = {
      route_key    = "GET /api/records/{recordId}/history"
      statement_id = "AllowHttpApiInvokeRecordHistory"
    }
    deprecation_request = {
      route_key    = "POST /api/records/{recordId}/deprecation-requests"
      statement_id = "AllowHttpApiInvokeDeprecationRequest"
    }
    # 画面上の通知（#15）。自分宛ての通知と未読件数の取得と、既読位置の更新
    notifications = {
      route_key    = "GET /api/notifications"
      statement_id = "AllowHttpApiInvokeNotifications"
    }
    notifications_read = {
      route_key    = "POST /api/notifications/read"
      statement_id = "AllowHttpApiInvokeNotificationsRead"
    }
    # 下書きの作成・一覧・取得・更新・申請と、操作結果の照会（#51）
    create_record = {
      route_key    = "POST /api/records"
      statement_id = "AllowHttpApiInvokeCreateRecord"
    }
    list_records = {
      route_key    = "GET /api/records"
      statement_id = "AllowHttpApiInvokeListRecords"
    }
    get_record = {
      route_key    = "GET /api/records/{recordId}"
      statement_id = "AllowHttpApiInvokeGetRecord"
    }
    update_record = {
      route_key    = "PATCH /api/records/{recordId}"
      statement_id = "AllowHttpApiInvokeUpdateRecord"
    }
    get_operation = {
      route_key    = "GET /api/operations/{operationId}"
      statement_id = "AllowHttpApiInvokeGetOperation"
    }
    submit_record = {
      route_key    = "POST /api/records/{recordId}/submit"
      statement_id = "AllowHttpApiInvokeSubmitRecord"
    }
  }
}

module "management_routes" {
  source                    = "../modules/management_routes"
  routes                    = local.management_routes
  authorization_scope       = var.portal_api_scope
  http_api_id               = var.http_api_id
  jwt_authorizer_id         = var.jwt_authorizer_id
  management_integration_id = var.management_integration_id
  management_function_name  = var.management_function_name
}
