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
| `backend/src/registry_portal/catalog.py` | カタログの一覧・検索・詳細 API。入力の検証、DTO への変換、サービス層の失敗の API エラーへの変換 |
| `backend/src/registry_portal/domain/catalog.py` | カタログ項目のモデル、種別（`AGENT`・`MCP`・`SKILL`）とデータ分類（`public`・`private`）、A2A を指定できる種別の規則。SPA の `CatalogRecordType`・`DataClassification` と値を揃える |
| `backend/src/registry_portal/services/discovery.py` | 発見アダプター。発見用 M2M トークンの取得・キャッシュと、JWT 認可の Registry の発見 API 呼び出し |
| `backend/src/registry_portal/services/catalog_supplement.py` | 補助テーブルから所有チームの正本と公開版のスナップショットを読む |
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
| `GET /api/catalog?type=AGENT\|MCP\|SKILL[&a2a=true][&nextToken=...]` | カタログ項目（公開承認済みの Registry レコード）を種別ごとに1ページ（最大50件）返す。a2a は Agent の一覧のみ指定可。ログインした全員（Viewer）が呼び出せる |
| `POST /api/catalog/search` | 自然言語・キーワード（必須）と絞り込み条件で検索し、関連度順に最大20件を返す。Viewer が呼び出せる |
| `GET /api/catalog/{recordId}` | 指定したレコード ID の公開中の版、所有チーム、その版の連絡先・利用条件・接続先の参照・関連参照を返す。Viewer が呼び出せる |

### カタログの一覧・検索・詳細

管理 handler は、利用者の権限を検査したうえで、発見用 M2M client のトークンを使って Registry の `ListDiscoverableRegistryRecords`（`POST /registries/{registryId}/discoverable-records-list`）を代理で呼び出します。JWT 認可の Registry は Boto3 では呼べないため、標準ライブラリの HTTP で呼びます（設計書「管理用の IAM と発見用の JWT を分ける」）。手で組み立てる一覧・検索・詳細の URL と payload は、固定版 Botocore のモデル（`agent-registry`）がシリアライズしたものと一致することをテストで確かめています。

- **入力**: Registry の ARN とリージョンは環境変数だけから決めます。一覧が受け付けるクエリパラメーターは `type`、`nextToken`、Agent 用の `a2a=true` です。検索条件は JSON 本文で指定します。検索と詳細ではクエリパラメーターを指定すると 400 `VALIDATION_ERROR` になります。
- **Agent 一覧の A2A フィルター**: 「A2A の Agent のみ」を選ぶと、一覧 API が AWS に `recordType=AGENT` と `descriptorType=a2aAgentCard` を指定します。絞り込みを切り替えると1ページ目に戻ります。[AWS の一覧フィルター仕様](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-browse-records.html)
- **ページング**: AWS の `nextToken` をそのまま中継します。受け取る `nextToken` は1〜4096文字の、空白・制御文字を含まない ASCII に限り、それ以外は Registry を呼ばずに 400 `INVALID_PAGE_TOKEN` とします。Registry が token を拒否した場合（`ValidationException` の `fieldList` に `nextToken` がある場合）も同じエラーにします。それ以外の 400 は、ポータル側の不具合として 502 `DISCOVERY_UNAVAILABLE` とします。AWS はページが疎であることを保証しないため、0件でも `nextToken` があれば SPA は次のページを促します。
- **M2M トークン**: client credentials で取得し、`expires_in` の60秒前までメモリー上で再利用します。改行などトークンとして不正な文字を含む値は、ヘッダーに載せずに 502 `DISCOVERY_UNAVAILABLE` とします。Registry が 401 を返したら1回だけ取り直して再試行し、それでも 401 なら 502 `DISCOVERY_UNAVAILABLE` とします。
- **client secret**: Boto3（`secretsmanager.get_secret_value`）で Secrets Manager から取得し、メモリー上だけに保持します。取得に失敗したら 502 `DISCOVERY_UNAVAILABLE` とし、ログには AWS のエラーコードだけを出します。SecretString が `client_secret` を持つ JSON ならその値を、そうでなければ文字列全体を使います。トークンエンドポイントが 400・401 を返したら、ローテーションに備えて次回は secret から取り直します。
- **障害と0件の区別**: Registry やトークンエンドポイントの失敗（5xx、429、403、タイムアウト、想定外の応答）は 502 `DISCOVERY_UNAVAILABLE` とし、0件の結果とは区別します。AWS のエラー内容は利用者に返しません。
- **ログ**: JWT、client secret、セッショントークン、応答の本文は出しません。出すのは HTTP ステータスと処理の種類だけです。

検索は `SearchDiscoverableRegistryRecords` を呼び出します。本文は `query`（必須、前後の空白を除いて1〜256文字）と任意の `type`（`AGENT`、`MCP`、`SKILL`）、`a2a`（真偽値）、`dataClassification`（`public`、`private`）を受け付けます。AWS の `searchQuery` は必須のため、検索語のない「条件だけの検索」は 400 `VALIDATION_ERROR` とします。条件だけで探す場合は一覧を使います。A2A の指定時は `recordType=AGENT` と `customMetadata.isA2A` を、分類の指定時は `customMetadata.dataClassification` を AWS の検索フィルターに含めます。AWS はこれらの条件を順位付け前に適用するため、対象全体から関連度順の最大20件を返します。返却順はそのまま保ちます。[AWS の検索フィルター仕様](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-search-records.html)

応答は `items` と `inconsistentA2ACount` です。A2A 検索で、検索用の写し（`isA2A`）と descriptor が食い違うレコードは、descriptor を正として結果から除外し、その件数を `inconsistentA2ACount` で返します。SPA は件数を警告として表示し、ほかの結果は表示します。

#### A2A 検索を有効にする

A2A の判定と検索用の写しの設計理由は [ADR-0006](../../docs/adr/0006-a2a-search-metadata-as-copy.md) を参照してください。A2A 検索は既定で無効です。無効の間は `a2a` を指定した検索に 503 `A2A_SEARCH_NOT_READY` を返し、SPA は一覧の「A2A の Agent のみ」へ案内します。

次の準備が終わってから、管理 Lambda の環境変数 `A2A_SEARCH_ENABLED` を `true` にしてください。このポータルは Registry の schema を変更しません。また、Registry への書き込みは ADR-0001 に従います。

1. **schema**: Admin が IaC で、対象 Registry の custom metadata schema の `AGENT` に `isA2A`（Boolean、Required）を追加します（設計書「共通項目と custom metadata」）。既存の共通フィールドと種別ごとのフィールドは残します。コンソールでの手作業の変更は行いません。[schema の定義](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-custom-metadata-schema.html)
2. **新しい版**: ポータルで作成・更新する版は、descriptor から `isA2A` を導出して公開する版と同時に保存します（#7・#8）。利用者の任意入力にはしません。
3. **既存の版**: 公開中の既存 `AGENT` レコードは、未管理レコードの取り込み（#16）で `isA2A` を補完し、通常の申請・公開承認の手順で公開します。ポータル外で補完する必要がある場合は、ADR-0001 の break-glass ロールだけで行います。その変更は「ポータル外の変更」として Admin への通知と監査の対象になります。schema を追加するだけでは、既存の公開版に値は補完されません。
4. **確認**: 検索インデックスへの反映後、`isA2A` を持たない公開中の `AGENT` が残っていないことを確認します。A2A の一覧（全ページ）と、`a2a=true` / `a2a=false` の検索結果を照合します。

#### 詳細

詳細は `BatchGetDiscoverableRegistryRecord` に ID を1件渡します。公開中の版が見つからなければ 404 `CATALOG_ITEM_NOT_FOUND` を返します。定義と SKILL.md はテキストとして表示し、HTML やスクリプトは実行しません。データ分類は公開中のレコードの `customMetadata.dataClassification` を表示し、`public`・`private` 以外の値は表示しません。

所有チームは、補助テーブルの管理情報（正本）から取得します。パーティションキー `pk` は `REGISTRY#{registryArn}#RECORD#{recordId}`、ソートキー `sk` は `MANAGEMENT` です。管理情報の `ownerTeam` は Cognito のチームグループ名を表す文字列です。未管理レコード（管理情報がない、または `managed` が `false`）と補助テーブルが未設定の場合は、公開中のレコードの `customMetadata.ownerTeam`（検索用の写し）を表示用に参照します。管理情報の読み取りに失敗した場合や、管理対象の項目に所有チームがない場合は 502 `DISCOVERY_UNAVAILABLE` を返します。

連絡先・利用条件・接続先の参照・関連参照は、補助テーブルに保存した公開中の版のスナップショット（[ADR-0007](../../docs/adr/0007-published-snapshot-for-detail.md)）から取得します。パーティションキー `pk` は管理情報と同じで、ソートキー `sk` は `PUBLISHED#{recordVersion}` です。申請・決裁処理（#9・#10）が、公開した版について次の属性を保存します。

| 属性 | 型 | 内容 |
|---|---|---|
| `contact` | S（任意） | 連絡先 |
| `termsOfUse` | S（任意） | 利用条件 |
| `connectionReference` | S（任意） | 接続先の参照 |
| `relatedReferences` | L（Agent は必須） | `recordId` と `recordVersion` の文字列を持つマップのリスト。Agent 以外は読みません |

スナップショットがない場合（補助テーブルが未設定、または未管理レコードなど）、API はこれらの値を `null` で返し、SPA は「未取得」と表示します。スナップショットはあるが属性がない場合は空文字で返し、「未登録」と表示します。関連参照は、空のリストなら「関連参照はありません」と表示します。Agent 以外の関連参照は常に空です。属性の型が契約と異なる場合は 502 `DISCOVERY_UNAVAILABLE` を返します。

デプロイ先の HTTP API で `POST /api/catalog/search` と `GET /api/catalog/{recordId}` を管理 Lambda に割り当て、既存の JWT authorizer を適用してください。SPA と API のオリジンが異なる場合は、CORS で `POST`、`Authorization`、`Content-Type` を許可してください。

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
| `SUPPLEMENT_TABLE_NAME` | （任意）Registry と同じリージョンにある補助 DynamoDB テーブル名。所有チームの正本と公開版のスナップショットを読む。管理 Lambda の実行ロールに対象テーブルへの `dynamodb:GetItem` 権限が必要 |
| `A2A_SEARCH_ENABLED` | （任意）`true` で A2A の絞り込み検索を有効にする。既定は無効。`isA2A` の準備が終わるまで有効にしない（「A2A 検索を有効にする」を参照）。`true`・`false` 以外は起動エラー |

必須の変数が未設定、または形式が誤っていれば起動エラーにします。開発用に認証を通す設定はありません。管理 Lambda の実行ロールには、`DISCOVERY_CLIENT_SECRET_ARN` に対する `secretsmanager:GetSecretValue` 権限が必要です（IaC は #5 で用意する）。Boto3・Botocore は Lambda ランタイム付属のものを使わず、`requirements.txt` で固定した版を同梱します（設計書「新しいサービス名前空間を使う」）。

### フロントエンド

`frontend/.env.example` を `frontend/.env.local` にコピーして値を設定します。必須項目が未設定なら、SPA はエラーを表示し、ログイン処理を行いません。

## 開発

初回の環境準備・キャッシュ設定・フックの導入は [開発環境の手順](../../docs/agents/development-environment.md) を参照してください。レビュー時の判断基準は [CODING_STANDARDS.md](CODING_STANDARDS.md) を参照してください。

```bash
# バックエンド
cd applications/registry_portal/backend
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest && flake8 src tests ../scripts && black --config pyproject.toml --check src tests ../scripts && mypy
python ../scripts/check_imports.py

# フロントエンド
cd applications/registry_portal/frontend
npm ci
npm run lint && npm test && npm run typecheck && npm run build
```

GitHub Actions（`.github/workflows/registry-portal-ci.yml`）では上記の検査だけを実行します。AWS の認証情報は持たせず、AWS への反映は行いません（ADR-0004）。

## custom Agent の下書き（#7）

Publisher は、所属する `team-*` を所有チームに指定して、フォームから custom Agent の下書きを作成・編集できます。同じチームの別メンバーも編集できます。チームのない Publisher、Viewer、未管理レコード、他チームのレコードへの取得・編集は `403 FORBIDDEN` です。Admin・Curator も Publisher グループを持たなければこの操作はできません。

フォームは名前、表示名、説明、semver の版、目的、接続先の参照、認証方式、連絡先、利用条件を受け付けます。所有チームは所属チームから選択し、environment は `dev`、データ分類は `public` / `private` に限定します。秘密情報の値は入力せず、接続先や認証情報の参照を記載してください。custom descriptor の `data` にはフォームの定義を canonical JSON として保存します。metadata は `ownerTeam`・`environment`・`dataClassification` と、定義から導出した真偽値 `isA2A=false` です。下書きの定義（連絡先・利用条件・接続先の参照を含む）は custom descriptor の中身を正本とし、画面にもこの値を表示します。連絡先・利用条件・接続先の参照は、公開版スナップショット（ADR-0007）を作るための写しとして補助ストアの管理情報（`MANAGEMENT`）にも保存し、`PUBLISHED#版` は上書きしません。写しが正本と食い違った場合は内容ハッシュが一致しなくなるため、更新は `409` になり、再読み込みして保存すると写しも正本に揃います。所有チームだけは補助ストアを正本とし、metadata の `ownerTeam` は検索用の写しです（ADR-0002、設計書「共通項目と custom metadata」）。

| API | 契約 |
| --- | --- |
| `POST /api/records` | フォームの JSON。`Idempotency-Key` 必須（英数字・`_`・`-`、1〜128文字）。同じ主体・キー・本文は同じ結果を返し、異なる本文なら409 |
| `GET /api/records` | 所属チームの管理レコードを一覧。`nextToken` をそのまま中継。途中のページが0件でも次のページがあり得る |
| `GET /api/records/{recordId}` | 下書きと `contentHash`、AWS の状態、公開中の版を返す |
| `PATCH /api/records/{recordId}` | `{ "draft": フォームのJSON, "expectedContentHash": 取得したハッシュ }`。所有チームと名前の変更は不可 |
| `GET /api/operations/{operationId}` | 所属チームの操作の結果を照合。`SUCCEEDED` / `VERIFYING` / `FAILED` を返す。補助ストアを読めない場合は `VERIFYING` ではなく502 |

作成はレコード ID が決まる前に名前単位の条件付きロックを取り、ID が判明したらレコード単位のロックも取ります。更新はレコード単位のロック内で所有情報と Registry を再取得し、Registry の内容と補助情報のハッシュを照合します。古いハッシュは `409 CONTENT_CONFLICT` とし、画面では入力を保持して再読み込みを促します。再読み込みは入力を最新内容に置き換えます。

書き込み前の操作意図、書き込み後の照合、結果とロック解除は DynamoDB の条件付きトランザクションで管理します。ロックに TTL は設定せず、期限切れを横取りの条件にしません。確定した冪等キーの結果も無期限に保持します。作成時に AWS の応答が不明なら同じ `clientToken` で再試行します。更新 API には `clientToken` がないため、結果不明の更新を再送せず照合します。AWS 成功後の補助ストア障害でも成功扱いにせず、ロックを維持します。

`202` は結果確認中です。「処理結果を確認」で再照合できます。`CREATING` / `UPDATING` の間は書き込みを止めます。AWS が保存を明示的に拒否した場合は400を返し、`CREATE_FAILED` / `UPDATE_FAILED` を確認した場合は操作を `FAILED` として保存し、対応するロックを解除します。補助ストアへ失敗結果を保存できなければ、確認できるまでロックを維持します。名前の変更など、AWS へ書き込む前に止めた更新は失敗を確定してロックを解除します。管理情報の型が壊れている場合は、所属外や0件として扱わず502を返します。

作成の通信結果が不明な場合は、同じ画面の「同じ入力とキーで作成を再試行」を使ってください。入力を固定し、同じ `Idempotency-Key`・本文・AWS `clientToken` で再送します。更新ではこの再送を行いません。失敗が確定するとフォームを再開し、次の作成は新しいキーで行います。ページを閉じた場合は、別キーで作り直す前に管理者へ連絡してください。

結果不明の操作は、次のように確定します。

- 更新: 「処理結果を確認」で照合します。AWS のレコードが更新後の内容になっていれば `SUCCEEDED`、`UPDATING` なら確認中のまま、更新前の内容のまま処理中でもなければ、更新が AWS に届かなかったとして `FAILED` を確定し、ロックを解除します。その後、同じ内容で保存し直せます。
- 作成（レコード ID が決まる前に失敗した場合）: 照合ではレコードを特定できないため確認中のままです。同じ入力とキーで作成を再試行すると、AWS の `clientToken` による冪等性で重複なく確定します。

どちらでも確定できない操作（補助ストア障害が続く場合など）を強制解除する回復手順は、Admin の回復操作として後続チケットで扱います。DynamoDB の `LOCK` や操作情報を手作業で消して再実行しないでください。

公開中の版より大きい semver かどうかは申請（#9）で検査し、下書きの保存では検査しません。公開中の版を補助ストアに保持するため、AWS が新しい下書きを返している間も「下書きあり」と「公開中」を併記します。公開承認・申請・A2A Agent Card・MCP・Skill の編集はこのチケットの対象外です。

`SUPPLEMENT_TABLE_NAME` は下書き操作では必須です。既存の `pk` / `sk` 文字列キーの補助テーブルを使います。管理 Lambda には [下書き用 Terraform モジュール](infrastructure/modules/management_drafts/main.tf) を接続してください。このモジュールは既存の管理 IAM ロール名、既存 Registry ARN、補助テーブル ARN を受け取り、Registry レコードの作成・更新・取得・一覧と、対象テーブルの GetItem / PutItem / DeleteItem だけを追加します。DynamoDB のトランザクションは含まれる各操作で認可されます。Registry 本体、schema、決裁、物理削除の権限は追加しません。このリポジトリにはまだ管理 Lambda のロールを定義するルートモジュールがないため、このモジュールの呼び出し（下の例）は #5 の IaC で追加します。それまで、このモジュールは単体で `terraform validate` と権限の内容のテストだけを行います。Registry とテーブルの ARN はリージョンを限定しません。API Gateway の CORS で `PATCH` と `Idempotency-Key` ヘッダーも許可してください。

```hcl
module "management_drafts" {
  source               = "./modules/management_drafts"
  management_role_name = aws_iam_role.management.name
  registry_arn         = var.registry_arn
  supplement_table_arn = aws_dynamodb_table.supplement.arn
}
```

作成と更新の AWS payload は別々に変換します。更新では `displayName` / `description` / `customMetadata` / `descriptors` と、custom 内部の `data` に `optionalValue` を使い、`recordVersion` は直接渡します。実サービスモデルの Stubber と moto の handler テストで、payload、排他、冪等性、他チーム拒否、補助情報の変更を含む409、AWS後の補助ストア障害を検証しています。SPA は `fetch` 境界で検証します。

公式資料は [CreateRegistryRecord](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_CreateRegistryRecord.html)、[UpdateRegistryRecord](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_UpdateRegistryRecord.html)、[GetRegistryRecord](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_GetRegistryRecord.html)、[版のライフサイクル](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-record-lifecycle.html)、[Registry IAM](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-iam-permissions.html)、[DynamoDB トランザクションの認可](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html) を参照してください。実 AWS への書き込みはこの実装検証では行っていません。デプロイ後に実環境で非同期状態・公開中の版と下書き・IAM 境界を確認してください。
