# dev の撤去は保護を一時的に外して行い、state バケットと bootstrap は残す

Registry ポータルの dev 環境を撤去するときは、追加スタック（`management`・`decision`・`notification_events`）、本体スタックの順に `scripts/deploy.sh --destroy` で消す。保護の設定は既定値で有効のまま保ち、撤去するときだけ一時的に外す。state バケットと bootstrap は撤去の対象に含めない（Issue #53）。

対象環境は IAM Identity Center のない個人 AWS アカウントで、`RegistryPortalDeployer` は `AdministratorAccess` を持つため、Terraform が作るリソースは権限の面ではすべて削除できる。destroy を止めているのは保護の設定だけなので、撤去のために保護を弱めた状態が残らないことを優先した。

| 対象 | 決定 | 理由 |
| --- | --- | --- |
| 補助テーブル | 削除保護（`deletion_protection_enabled`）を変数 `table_deletion_protection`（既定値 `true`）にする。`lifecycle { prevent_destroy = true }` は撤去するときだけ手元で行を外し、その変更はコミットしない | `prevent_destroy` は Terraform の制約で変数にできない（lifecycle の設定にはリテラル値しか使えない。[Terraform の lifecycle](https://developer.hashicorp.com/terraform/language/meta-arguments/lifecycle)）。PITR が有効なので、テーブルを削除すると DynamoDB が 35 日保持のシステムバックアップを自動で作る（[DynamoDB のドキュメント](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/PointInTimeRecovery_Howitworks.html#howitworks-pitr-deleting-table)）。データの退避はこれに任せ、必要な場合だけオンデマンドバックアップを取る |
| SPA バケット | `force_destroy` を変数 `spa_force_destroy`（既定値 `false`）にする。撤去の前に `true` で apply してから destroy する | S3 は空のバケットしか削除できず（[S3 のドキュメント](https://docs.aws.amazon.com/AmazonS3/latest/userguide/delete-bucket.html)）、バージョニングが有効なため旧バージョンも残る。`force_destroy` は state に入った値が destroy に効くため、destroy の前に apply が必要になる（[AWS provider 6.71.0 の aws_s3_bucket](https://registry.terraform.io/providers/hashicorp/aws/6.71.0/docs/resources/s3_bucket)）。全バージョンを消す手作業の手順より、変数の切り替えのほうが誤りにくい |
| CloudWatch Logs | `skip_destroy` は付けない。撤去するとロググループも消える | dev は作り直しを前提にする。`skip_destroy` で残したロググループは、作り直すときに同じ名前で作成できず、import が必要になる |
| state バケットと bootstrap | 撤去の対象に含めない。dev の作り直しは追加スタックと本体に限る | state バケットは全スタックの state を持ち、作り直しの土台になる。bootstrap の state はローカルにあり、`prevent_destroy` と `force_destroy = false` で保護している |

## 検討した選択肢

- 補助テーブルの `prevent_destroy` を外したままにする案は採用しない。保護は既定値で有効に保ち、外した編集がコミットされたことを `infrastructure/tests/test_teardown_guards.py` で検出する。
- 補助テーブルのデータを撤去のたびに S3 へエクスポートする案は採用しない。退避は PITR のシステムバックアップに任せ、必要な場合だけ撤去の前にオンデマンドバックアップを取る。
- SPA バケットの全バージョンを AWS CLI で消す手順を書く案は採用しない。変数で `force_destroy` を切り替える。

## 影響

- 撤去の順番は「追加スタック → 本体」である。追加スタックは本体の HTTP API・管理 Lambda・実行ロール・補助テーブルを借りて route・Lambda permission・inline policy を足しており、本体を先に消すと追加スタックの state に孤立したリソースが残る。`deploy.sh --destroy` は、本体を撤去する前に追加スタックの state が空であることを確かめ、残っていれば止まる。
- 撤去の後は、`main.tf` の `prevent_destroy` の編集と tfvars の切り替えを元に戻す。保護を外す apply の後で撤去を中止した場合や、destroy が途中で失敗した場合も、撤去を続けるか、既定値で apply し直して保護を戻す。手順は [Terraform ガイドの 5.4](../agents/registry-portal-terraform-guide-ja.md#54-dev-環境を撤去する) に従う。
- 撤去で消えたロググループは戻らない。必要なログは撤去の前に取得する。
- 既存 Registry、Cognito User Pool 本体、手動で作った IAM の主体、Discovery 用の Secrets Manager と KMS キーは Terraform の管理対象ではないため、撤去後も残る。
