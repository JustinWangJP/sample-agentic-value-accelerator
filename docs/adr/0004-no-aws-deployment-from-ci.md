# CI から AWS へは反映せず、反映はローカルから手動で行う

GitHub Actions では、テスト、Linter、型検査、`terraform fmt -check` / `validate` だけを実行する。AWS の認証情報（OIDC ロールを含む）は CI に一切持たせない。AWS への反映は、オペレーターがローカル端末から IAM Identity Center の短期認証情報で `scripts/deploy.sh` を手動で実行して行う。一般的な CI/CD とは逆の方針だが、AWS に影響する経路を自動化された外部システムに持たせないことを優先した。

IAM Identity Center のない個人 AWS アカウントでは、MFA 付きで引き受けた IAM ロール `RegistryPortalDeployer` の短期認証情報を使う。AWS CLI のロールプロファイルと、`credential_process` で同じセッションを渡す Terraform 用プロファイルを用意する。bootstrap は Identity Center の資源を任意とし、state バケットの主体を `deployer_principal_arn` で指定する。

個人アカウントの MFA ロール経路は、本体と別 state の追加 3 スタックを対象とする。bootstrap の SSO permission set は本体向けの権限だけを持ち、追加スタックの state key や決裁・イベント処理の資源には対応しない。SSO 経路で追加スタックを配信する場合は、別途権限整備が必要である。

## Consequences

- デプロイ用の permission set（`RegistryPortalDeployer`）では、Registry レコードの書き込み系操作と、Registry 本体の更新・削除を Deny する（ADR-0001 と整合させるため）。
- 安全策は CI ではなくデプロイスクリプト側に持たせる。具体的には、plan の目視確認を必須にし、既存 Registry への update / destroy が含まれていれば中断する。
- Terraform state は S3 backend（S3 ネイティブのロック）に置き、担当者が替わっても引き継げるようにする。
- 個人アカウントの IAM ロールは `AdministratorAccess` と明示的 Deny を組み合わせるため、SSO permission set より権限が広い。各スタックの保存済み plan を JSON ガードに通し、IAM の対象ロール・ARN・action を目視確認する。[配信手順](../agents/registry-portal-terraform-guide-ja.md)と [state 分割の決定](0013-separate-portal-addon-states.md)に従う。
