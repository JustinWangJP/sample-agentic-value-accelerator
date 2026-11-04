# Registry ポータル

AWS Agent Registry を唯一の正本として、Agent・MCP サーバー・Skill の登録、公開承認、発見を扱う管理ポータルです。用語は [GLOSSARY.md](../../GLOSSARY.md)、設計は [MVP1 設計](../../plan/aws-agent-registry-mvp1-design-ja.md) と `docs/adr/` に従います。

既存の Control Plane とはコードを共有しない、独立したアプリです。

## 構成

| パス | 内容 |
|---|---|
| `backend/src/registry_portal/handlers/management.py` | 管理 handler の Lambda エントリーポイント。認証設定がなければ初期化に失敗する |
| `backend/src/registry_portal/app.py` | API Gateway HTTP API（payload format 2.0）のイベントを受け取り、route を振り分ける |
| `backend/src/registry_portal/auth.py` | 認可。検証済みの claims から主体・業務ロール・所属チームを導出する |
| `backend/src/registry_portal/errors.py` | 共通エラー DTO と日本語メッセージ |
| `backend/src/registry_portal/catalog.py` | カタログ一覧 API。クエリの検証、DTO への変換、発見アダプターの失敗の API エラーへの変換 |
| `backend/src/registry_portal/domain/catalog.py` | カタログ項目のモデルと種別（`AGENT`・`MCP`・`SKILL`）。SPA の `CatalogRecordType` と値を揃える |
| `backend/src/registry_portal/services/discovery.py` | 発見アダプター。発見用 M2M トークンの取得・キャッシュと、JWT 認可の Registry の発見 API 呼び出し |
| `backend/src/registry_portal/services/http.py` | 標準ライブラリによる HTTP トランスポート。テストではフェイクに差し替える |
| `frontend/` | React SPA。Authorization Code + PKCE でログインし、アクセストークンはメモリー上だけで保持する |

## 認可

管理 handler は、HTTP API の JWT authorizer が検証した claims（`requestContext.authorizer.jwt.claims`）を、Lambda でも次のとおり検査します。

- `token_use` が `access` であること（ID トークンは拒否する）
- `iss` が設定した Cognito User Pool であること
- `client_id` が人間用 SPA client であること（発見用 M2M client などは拒否する）
- `sub` があること

主体は `iss` + `sub` で識別します。業務ロールは Cognito グループから導出します。

| Cognito グループ | 業務ロール |
|---|---|
| （なし。ログインした全員） | Viewer |
| `registry-publisher` | Publisher |
| `registry-curator` | Curator |
| `registry-admin` | Admin |
| `team-*` | 所属チーム |

`registry-publisher` を持てば、`team-*` がなくても業務ロールは Publisher と表示します。下書きの作成・編集・申請・廃止依頼では、対象の所有チームが所属チームに含まれることを別途検査します（後続の #7・#9・#13 で実装。設計書「ロールと所有」）。

メールアドレスや `x-user-email` などのリクエストヘッダーは、主体や権限の判定に使いません。

認証できないトークン（トークンなし、ID トークン、別 issuer、`sub` なし）は 401 `UNAUTHENTICATED` で拒否します。署名は正しいが人間用 SPA client 以外に発行されたトークン（別 client、発見用 M2M client）は、ログインし直しても解決しないため 403 `FORBIDDEN` で拒否します。SPA が「もう一度ログインする」を表示するのは、401 とログインの失敗のときだけです。API Gateway の JWT authorizer が返す 401（共通 DTO ではない）も、同じ認証エラーとして扱います（設計書「401 と 403 の使い分け」）。

### API

| API | 内容 |
|---|---|
| `GET /api/me` | 自分の主体、業務ロール、所属チーム |
| `GET /api/catalog?type=AGENT\|MCP\|SKILL[&nextToken=...]` | カタログ項目（公開承認済みの Registry レコード）を種別ごとに1ページ（最大50件）返す。ログインした全員（Viewer）が呼び出せる |

### カタログ一覧と発見アダプター

管理 handler は、利用者の権限を検査したうえで、発見用 M2M client のトークンを使って Registry の `ListDiscoverableRegistryRecords`（`POST /registries/{registryId}/discoverable-records-list`）を代理で呼び出します。JWT 認可の Registry は Boto3 では呼べないため、標準ライブラリの HTTP で呼びます（設計書「管理用の IAM と発見用の JWT を分ける」）。手で組み立てる URL と payload は、固定版 Botocore のモデル（`agent-registry`）がシリアライズしたものと一致することをテストで確かめています。

- **Registry と接続先**: Registry の ARN とリージョンは環境変数だけから決めます。`type` と `nextToken` 以外のクエリパラメーター（`registryId`、`endpoint` など）は 400 `VALIDATION_ERROR` で拒否します。
- **ページング**: AWS の `nextToken` をそのまま中継します。受け取る `nextToken` は1〜4096文字の、空白・制御文字を含まない ASCII に限り、それ以外は Registry を呼ばずに 400 `INVALID_PAGE_TOKEN` とします。Registry が token を拒否した場合（`ValidationException` の `fieldList` に `nextToken` がある場合）も同じエラーにします。それ以外の 400 は、ポータル側の不具合として 502 `DISCOVERY_UNAVAILABLE` とします。AWS はページが疎であることを保証しないため、0件でも `nextToken` があれば SPA は次のページを促します。
- **M2M トークン**: client credentials で取得し、`expires_in` の60秒前までメモリー上で再利用します。改行などトークンとして不正な文字を含む値は、ヘッダーに載せずに 502 `DISCOVERY_UNAVAILABLE` とします。Registry が 401 を返したら1回だけ取り直して再試行し、それでも 401 なら 502 `DISCOVERY_UNAVAILABLE` とします。
- **client secret**: Boto3（`secretsmanager.get_secret_value`）で Secrets Manager から取得し、メモリー上だけに保持します。取得に失敗したら 502 `DISCOVERY_UNAVAILABLE` とし、ログには AWS のエラーコードだけを出します。SecretString が `client_secret` を持つ JSON ならその値を、そうでなければ文字列全体を使います。トークンエンドポイントが 400・401 を返したら、ローテーションに備えて次回は secret から取り直します。
- **障害と0件の区別**: Registry やトークンエンドポイントの失敗（5xx、429、403、タイムアウト、想定外の応答）は 502 `DISCOVERY_UNAVAILABLE` とし、0件の結果とは区別します。AWS のエラー内容は利用者に返しません。
- **ログ**: JWT、client secret、セッショントークン、応答の本文は出しません。出すのは HTTP ステータスと処理の種類だけです。

エラーは共通 DTO で返します。

```json
{ "error": { "code": "UNAUTHENTICATED", "message": "認証が必要です。ログインし直してください。", "requestId": "..." } }
```

## 設定

### バックエンド（Lambda の環境変数）

| 変数 | 内容 |
|---|---|
| `COGNITO_ISSUER` | Cognito User Pool の issuer（`https://cognito-idp.us-east-1.amazonaws.com/<pool-id>`） |
| `HUMAN_CLIENT_ID` | 人間用の公開 SPA client の client_id |
| `REGISTRY_ARN` | 既存 Registry の ARN（`arn:aws:agent-registry:<region>:<account>:registry/<id>`）。リージョンは ARN から導出する |
| `DISCOVERY_TOKEN_URL` | 発見用 M2M client のトークンエンドポイント（`https://<domain>/oauth2/token`）。https に限る |
| `DISCOVERY_CLIENT_ID` | 発見用 M2M client の client_id |
| `DISCOVERY_CLIENT_SECRET_ARN` | 発見用 M2M client の secret を保管した Secrets Manager の ARN。Secrets Manager のリージョンは ARN から導出する |
| `DISCOVERY_SCOPE` | （任意）トークン取得時に要求する scope。空なら client の既定 |

`DISCOVERY_SCOPE` 以外が未設定、または形式が誤っていれば起動エラーにします。開発用に認証を通す設定はありません。管理 Lambda の実行ロールには、`DISCOVERY_CLIENT_SECRET_ARN` に対する `secretsmanager:GetSecretValue` 権限が必要です（下記の専用 Terraform で設定する）。Boto3・Botocore は Lambda ランタイム付属のものを使わず、`requirements.txt` で固定した版を同梱します（設計書「新しいサービス名前空間を使う」）。

### フロントエンド

`frontend/.env.example` を `frontend/.env.local` にコピーして値を設定します。必須項目が未設定なら、SPA はエラーを表示し、ログイン処理を行いません。

## 開発

```bash
# バックエンド
cd applications/registry_portal/backend
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest && flake8 src tests && black --check src tests && mypy

# フロントエンド
cd applications/registry_portal/frontend
npm ci
npm run lint && npm test && npm run typecheck && npm run build
```

GitHub Actions（`.github/workflows/registry-portal-ci.yml`）では上記に加え、Terraform と配信スクリプトのローカル検査を実行します。AWS の認証情報は持たせず、AWS への反映は行いません（ADR-0004）。


## dev に手動配信する

前提は [ADR-0003](../../docs/adr/0003-public-https-without-waf-and-single-registry.md) と [ADR-0004](../../docs/adr/0004-no-aws-deployment-from-ci.md) に従います。実行には Terraform 1.10 以上、Python 3.13 と pip、Node.js 24 と npm、AWS CLI v2 が必要です。AWS provider は検証した 6.71.0 に固定しています。

Issue #2 の読み取り確認で、既存 Registry、Cognito User Pool、SSO の IdP 名、発見用 M2M client と secret ARN を確定してください。既存 Registry と User Pool 本体は Terraform の管理対象にしません。User Pool の既存ドメインを使い、ポータル用の client、リソースサーバー、業務ロールと `team-*` グループだけを追加します。既に同名のグループや `registry-portal` リソースサーバーがある場合は、その所有者と調整して import してから plan を確認してください。

1. 初回だけ、Identity Center と state バケットを管理できる短期認証情報で bootstrap を実行します。`infrastructure/bootstrap/terraform.tfvars.example` を同ディレクトリの `terraform.tfvars` にコピーし、値を置き換えます。

   ```bash
   cd applications/registry_portal
   terraform -chdir=infrastructure/bootstrap init
   terraform -chdir=infrastructure/bootstrap plan -out=bootstrap.tfplan
   terraform -chdir=infrastructure/bootstrap show bootstrap.tfplan
   terraform -chdir=infrastructure/bootstrap apply bootstrap.tfplan
   ```

   bootstrap は state バケットと `RegistryPortalDeployer` permission set、その既存グループへの割当てを作ります。state バケットはバージョニング、暗号化、公開アクセス禁止を有効にし、state とロックへのアクセスをこの permission set の SSO ロールに限定します。bootstrap 自身の state は初回管理者が暗号化した場所で保管してください。本体の state と混ぜません。permission set は Registry 本体とレコードへの書き込みを明示的に Deny します。CloudFront の作成や API Gateway の一覧など、作成前に ARN がない操作にはサービスに限定した権限を使います。管理 Lambda の実行ロールを渡せるのは Lambda サービスだけです。

2. `infrastructure/backend.hcl.example` と `infrastructure/terraform.tfvars.example` を、それぞれ `infrastructure/backend.hcl` と `infrastructure/dev.tfvars` にコピーします。bootstrap と同じアカウント、バケット名、名前の接頭辞を設定します。secret 値は入力しません。既存 secret が顧客管理 KMS キーを使う場合だけ `discovery_kms_key_arn` を設定します。管理 Lambda には、そのキーの復号を Secrets Manager 経由かつ対象 secret の暗号化コンテキストに限定して付けます。

3. AWS CLI の SSO profile に `RegistryPortalDeployer` を設定し、ログインして配信します。環境変数のアクセスキーは解除してください。profile の `sso_region` は Identity Center の既存リージョンを使います。

   ```bash
   aws sso login --profile registry-portal-dev
   AWS_PROFILE=registry-portal-dev scripts/deploy.sh infrastructure/backend.hcl infrastructure/dev.tfvars
   ```

   スクリプトは SDK 同梱 ZIP を生成し、S3 backend の暗号化と `use_lockfile=true` を強制して初期化します。保存した plan を JSON ガードに通し、同じ plan を表示します。内容を確認して `apply` と入力すると、その保存済み plan だけを反映します。中止、ガード違反、途中のコマンド失敗では後続の操作を行いません。CI 内からの実行と長期アクセスキーは拒否します。

4. apply 後は Terraform の `frontend_config` 出力を使って SPA をビルドします。`openid registry-portal/catalog.read` を含む scope と専用の HTTP API URL が設定されます。ハッシュ付きアセットを S3 に同期し、最後に `index.html` を非キャッシュで配置して CloudFront を無効化します。生成 ZIP と plan JSON は一時ディレクトリから削除します。

CloudFront の接続先は非公開 S3 の REST endpoint だけです。深い SPA URL は viewer request function で `index.html` に解決します。API は独立した HTTP API origin で提供し、CloudFront のエラー差し替えは使いません。CloudFront 側に誤って `/api/*` を送った場合も JSON の 404 を返します。CSP の `connect-src` は Cognito の指定ドメインと us-east-1 の HTTP API ドメインを許可します。API の CORS は当該 CloudFront origin だけに限定します。この依存が循環しないよう、CSP の HTTP API 許可先はリージョン単位です。

HTTP API は `GET /api/me` と `GET /api/catalog` を JWT authorizer と `registry-portal/catalog.read` で保護します。人間用 client は code flow のみを許可し、SPA が PKCE を使用します。人間用 client に secret はありません。Lambda permission は各 GET route の API Gateway 実行 ARN とアカウントに限定します。Function URL と VPC は作りません。管理 Lambda 以外の実行ロールはこの実装単位では作りません。決裁・イベント処理は後続 Issue で追加します。

補助 DynamoDB はオンデマンド、暗号化、PITR、削除保護を有効にします。現時点の閲覧 handler には DynamoDB の権限を付けません。管理 Lambda の IAM はログ、対象 Registry の読み取り、発見用 secret の取得だけです。Registry の発見は IAM ではなく既存の M2M JWT で行います。CloudWatch Logs は API と Lambda とも365日保存し、ログ書式にトークンや応答本文を含めません。

### 反映前ガードをローカルで検査する

```bash
cd applications/registry_portal
python3 -m unittest discover -s infrastructure/tests -v
node --test infrastructure/tests/spa_rewrite.test.mjs
bash -n scripts/deploy.sh
terraform -chdir=infrastructure fmt -check -recursive
terraform -chdir=infrastructure init -backend=false
terraform -chdir=infrastructure validate
terraform -chdir=infrastructure test
terraform -chdir=infrastructure/bootstrap init -backend=false
terraform -chdir=infrastructure/bootstrap validate
terraform -chdir=infrastructure/bootstrap test
```

これらは AWS の認証情報を必要としません。Terraform test は mock provider を使います。JSON ガードは `python3 scripts/check_plan.py <plan.json>` で再実行できます。既存 Registry の create、update、delete、置換、禁止 action の Allow、managed policy の付与、未確定 IAM policy、Function URL を拒否します。Allow の `NotAction` と全権限ワイルドカードも拒否し、Deny 文は許可します。将来の登録・決裁用 IAM を追加するときは、担当 Issue でガードの許可境界と fixture も更新してください。

### dev の手動受入を記録する

この変更では AWS API の呼び出し、bootstrap、plan、apply、アップロード、CloudFront の無効化を実行していません。Issue #2 の読み取り確認も未完了です。次の項目は AWS 権限を持つオペレーターが dev に反映してから Issue #5 に結果を記録します。

- [ ] SSO と PKCE でログインし、`/api/me` とカタログ一覧が表示される。M2M トークン取得から Registry の発見 API まで到達する。
- [ ] User Pool で業務ロールと `team-*` の所属を割り当て、画面の主体と一致する。
- [ ] S3 のオブジェクトを匿名で直接取得すると拒否される。CloudFront では取得できる。
- [ ] SPA の深い URL を再読み込みでき、API の 401、403、404 が HTML の成功応答にならない。
- [ ] scope 不足、ID トークン、別 issuer、別 client、発見用 M2M client のトークンで API を呼び出せない。
- [ ] API と Lambda のログに JWT、secret、応答本文が出ていない。
- [ ] Terraform plan に既存 Registry の作成・変更・削除がなく、専用 permission set と実行ロールの IAM が意図した ARN と action に限定される。
