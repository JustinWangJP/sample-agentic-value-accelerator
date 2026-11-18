# 本体と追加スタックの state を分けて運用する

Registry ポータルの本体スタックと、`management`・`decision`・`notification_events` の追加スタックは、別の Terraform state で運用する。Issue #5・#49 の統合では state を移動しない。本体の資源を借りる ID・名前・ARN は本体の output から取得し、オペレーターが各追加スタックの tfvars に手動で転記する。

本体は SPA、HTTP API、JWT authorizer、管理 Lambda とその統合、補助テーブル、Cognito の子リソースを所有する。`management` は管理 Lambda の実行ロールに付ける追加ポリシーと下書き・申請・操作照会を含む管理 handler の追加 route を所有する。`decision` は決裁 Lambda と決裁 route を、`notification_events` はイベント処理 Lambda・EventBridge ルール・DLQ を所有する。本体から同じ route を重ねて定義しない。

この分割は、既存の追加スタックを本体の構成に接続するための決定である。配信時に state の移行を重ねず、各スタックの plan を個別に確認できる形を維持する。

## 検討した選択肢

- 本体からすべての追加モジュールを呼び、同じ state に統合する案は今回は採用しない。既存資源の所有を移す作業が必要になるため、後続 Issue で扱う。
- `terraform_remote_state` で自動連携する案は今回は採用しない。連携値は本体の output と tfvars で明示し、オペレーターが転記する。

## 影響

- 本体を先に配信し、output を転記してから追加スタックを配信する。連携する資源を変更した場合は、各 tfvars も更新する。
- state key は `registry-portal/dev/terraform.tfstate`、`registry-portal/dev/management.tfstate`、`registry-portal/dev/decision.tfstate`、`registry-portal/dev/notification-events.tfstate` を使う。bootstrap 自身の state は別に保管する。
- 各スタックで保存した plan を同じ `check_plan.py` に通し、目視確認後にその plan だけを apply する。ガードは policy の資源アドレスで役割を判定するため、`management_role_name` の取り違えは検出できない。本体の output と plan の対象ロールを照合する。
- 後続 Issue で統合する場合だけ、移行先に対象資源を import し、移行元で `terraform state rm` を実行する。移行中は apply を止め、同じ資源を複数の state から操作しない。両方の plan に作り直しや削除がないことを確認してから配信を再開する。
- dev への AWS apply と手動受入は未実施である。配信手順と検証項目は [Terraform ガイド](../agents/registry-portal-terraform-guide-ja.md)と [README](../../applications/registry_portal/README.md#dev-に手動配信する)に従う。
