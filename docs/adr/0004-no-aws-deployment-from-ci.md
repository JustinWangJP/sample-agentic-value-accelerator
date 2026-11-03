# CI から AWS へは反映せず、反映はローカルから手動で行う

GitHub Actions では、テスト、Linter、型検査、`terraform fmt -check` / `validate` だけを実行する。AWS の認証情報（OIDC ロールを含む）は CI に一切持たせない。AWS への反映は、オペレーターがローカル端末から IAM Identity Center の短期認証情報で `scripts/deploy.sh` を手動で実行して行う。一般的な CI/CD とは逆の方針だが、AWS に影響する経路を自動化された外部システムに持たせないことを優先した。

## Consequences

- デプロイ用の permission set（`RegistryPortalDeployer`）では、Registry レコードの書き込み系操作と、Registry 本体の更新・削除を Deny する（ADR-0001 と整合させるため）。
- 安全策は CI ではなくデプロイスクリプト側に持たせる。具体的には、plan の目視確認を必須にし、既存 Registry への update / destroy が含まれていれば中断する。
- Terraform state は S3 backend（S3 ネイティブのロック）に置き、担当者が替わっても引き継げるようにする。
