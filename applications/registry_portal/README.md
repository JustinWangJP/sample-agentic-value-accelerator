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
| `backend/src/registry_portal/drafts.py` | custom Agent の下書きの作成・編集・一覧・操作の照合 API。排他・冪等性と、AWS の失敗の API エラーへの変換 |
| `backend/src/registry_portal/domain/drafts.py` | 下書きのフォーム入力・管理情報のモデル、AWS の作成用・更新用 payload への変換、状態と environment の値。SPA の `frontend/src/drafts/api.ts` と値を揃える |
| `backend/src/registry_portal/domain/submissions.py` | 申請入力・semver の比較・申請スナップショットの構築 |
| `backend/src/registry_portal/services/draft_store.py` | 補助テーブルの下書き用の項目（管理情報・ロック・操作・操作記録）の読み書き |
| `infrastructure/` | 下書きの IAM モジュール（`modules/management_drafts`）と、既存ロールへ適用するルートモジュール（`management`） |
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

`registry-publisher` を持てば、`team-*` がなくても業務ロールは Publisher と表示します。下書きの作成・編集・申請・廃止依頼では、対象の所有チームが所属チームに含まれることを別途検査します（作成・編集・申請は実装済み、廃止依頼は #13。設計書「ロールと所有」）。

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
| `SUPPLEMENT_TABLE_NAME` | （任意。下書きの管理には必須）Registry と同じリージョンにある補助 DynamoDB テーブル名。所有チームの正本と公開版のスナップショットを読み、下書きの管理情報・ロック・操作を保存する。カタログだけなら `dynamodb:GetItem`、下書きには「custom Agent の下書き」の IAM が必要。未設定なら下書きの API は 503 |
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

フォームは名前、表示名、説明、semver の版、目的、接続先の参照、認証方式、連絡先、利用条件を受け付けます。所有チームは所属チームから選択し、environment は `dev`、データ分類は `public` / `private` に限定します。秘密情報の値は入力せず、接続先や認証情報の参照を記載してください。custom descriptor の `data` にはフォームの定義を canonical JSON として保存します。metadata は `ownerTeam`・`environment`・`dataClassification` と、定義から導出した真偽値 `isA2A=false` です。下書きの定義（連絡先・利用条件・接続先の参照を含む）は custom descriptor の中身を正本とし、画面にもこの値を表示します。連絡先・利用条件・接続先の参照は、公開版スナップショット（ADR-0007）を作るための写しとして補助ストアの管理情報（`MANAGEMENT`）にも保存し、`PUBLISHED#版` は上書きしません。写しが正本と食い違った場合は内容ハッシュが一致しなくなるため、更新は `409` になり、再読み込みして保存すると写しも正本に揃います（[ADR-0008](../../docs/adr/0008-draft-definition-in-custom-descriptor.md)）。所有チームだけは補助ストアを正本とし、metadata の `ownerTeam` は検索用の写しです（ADR-0002、設計書「共通項目と custom metadata」）。

保存に成功すると、管理情報に最終編集者（`lastEditedBy` = `{issuer, subject}`）と保存日時（`lastEditedAt`）も記録します。申請（#9）と決裁（#10）は、自己承認の禁止にこの値を使います（設計書「ロールと所有」）。

版は semver で指定します。ただし AWS の `recordVersion` は `[a-zA-Z0-9.-]+` に限られ `+` を受け付けないため、ビルドメタデータ（`1.0.0+build`）は AWS に送る前に 400 とします（[CreateRegistryRecord](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_CreateRegistryRecord.html) の `recordVersion`、固定版 Botocore の `RegistryRecordVersion`）。

| API | 契約 |
| --- | --- |
| `POST /api/records` | フォームの JSON。`Idempotency-Key` 必須（英数字・`_`・`-`、1〜128文字）。同じ主体・キー・本文は同じ結果を返し、異なる本文なら409 |
| `GET /api/records` | 所属チームの管理レコードを一覧。`nextToken` をそのまま中継。途中のページが0件でも次のページがあり得る |
| `GET /api/records/{recordId}` | 下書きと `contentHash`、AWS の状態、公開中の版を返す |
| `PATCH /api/records/{recordId}` | `{ "draft": フォームのJSON, "expectedContentHash": 取得したハッシュ }`。所有チームと名前の変更は不可 |
| `GET /api/operations/{operationId}` | 所属チームの操作の結果を照合。`SUCCEEDED` / `VERIFYING` / `FAILED` を返す。補助ストアを読めない場合は `VERIFYING` ではなく502 |

下書きの API が返す主なエラーは次のとおりです。SPA は、AWS に届いたかどうか分からない失敗（通信断と 502 など）だけを「結果が不明」と表示し、それ以外は入力を残して保存し直せるようにします。

| 状態 | コード | 意味と利用者の対応 |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | 入力の誤り、または AWS が入力や設定を理由に保存を拒否した。入力を直して保存する |
| 409 | `CONTENT_CONFLICT` | 内容ハッシュの不一致、または別の操作が処理中。再読み込みしてから保存する |
| 429 | `REGISTRY_THROTTLED` | AWS が混雑で保存を処理しなかった（`ThrottlingException`）。入力は正しいため、時間をおいて同じ内容で保存する |
| 502 | `REGISTRY_UNAVAILABLE` | Registry や補助ストアを読めない、または保存内容の型が壊れている |
| 503 | `DRAFTS_NOT_CONFIGURED` | `SUPPLEMENT_TABLE_NAME` が未設定で、下書きの管理を提供していない。どの下書き API でも同じ応答にする。管理者が設定する |

作成はレコード ID が決まる前に名前単位の条件付きロックを取り、ID が判明したらレコード単位のロックも取ります。更新はレコード単位のロック内で所有情報と Registry を再取得し、Registry の内容と補助情報のハッシュを照合します。古いハッシュは `409 CONTENT_CONFLICT` とし、画面では入力を保持して再読み込みを促します。再読み込みは入力を最新内容に置き換えます。

書き込み前の操作意図、書き込み後の照合、結果とロック解除は DynamoDB の条件付きトランザクションで管理します。ロックに TTL は設定せず、期限切れを横取りの条件にしません。ロックは操作の結果が確定したときに削除します。確定した操作（実行制御の `OP#…` / `CONTROL`）には TTL 属性 `expiresAt` を付け、7日後に DynamoDB が削除します（設計書「補助テーブルに保存するもの」の「確定後に TTL で削除」）。この間は同じ `Idempotency-Key` に同じ結果を返します。結果確認中の操作には TTL を付けません。操作記録（`AUDIT#…`）は無期限に保持し、AWS の書き込み API が返した request ID（`awsRequestId`）も結果に記録します。作成時に AWS の応答が不明なら同じ `clientToken` で再試行します。更新 API には `clientToken` がないため、結果不明の更新を再送せず照合します。AWS 成功後の補助ストア障害でも成功扱いにせず、ロックを維持します。

`202` は結果確認中です。「処理結果を確認」で再照合できます。`CREATING` / `UPDATING` の間は書き込みを止めます。AWS が書き込みを処理せずに拒否したと確定できる場合は、操作を `FAILED` として確定し、ロックを解除します。入力や設定を理由とする拒否（`ValidationException`・`AccessDeniedException`・`ConflictException`・`ResourceNotFoundException`・`ServiceQuotaExceededException`）は400、混雑（`ThrottlingException`）は429を返します。作成では、どちらの場合も次の保存に新しい `Idempotency-Key` を使います。`CREATE_FAILED` / `UPDATE_FAILED` を確認した場合は操作を `FAILED` として保存し、対応するロックを解除します。補助ストアへ失敗結果を保存できなければ、確認できるまでロックを維持します。名前の変更など、AWS へ書き込む前に止めた更新は失敗を確定してロックを解除します。管理情報の型が壊れている場合は、所属外や0件として扱わず502を返します。

作成の通信結果が不明な場合は、同じ画面の「同じ入力とキーで作成を再試行」を使ってください。入力を固定し、同じ `Idempotency-Key`・本文・AWS `clientToken` で再送します。更新ではこの再送を行いません。失敗が確定するとフォームを再開し、次の作成は新しいキーで行います。ページを閉じた場合は、別キーで作り直す前に管理者へ連絡してください。

結果不明の操作は、次のように確定します。

- 更新: 「処理結果を確認」で照合します。AWS のレコードが更新後の内容になっていれば `SUCCEEDED`、`UPDATING` なら確認中のまま、更新前の内容のまま処理中でもなければ、更新が AWS に届かなかったとして `FAILED` を確定し、ロックを解除します。その後、同じ内容で保存し直せます。
- 作成（レコード ID が決まる前に失敗した場合）: 照合ではレコードを特定できないため確認中のままです。同じ入力とキーで作成を再試行すると、AWS の `clientToken` による冪等性で重複なく確定します。

どちらでも確定できない操作（補助ストア障害が続く場合など）を強制解除する回復手順は、Admin の回復操作として後続チケットで扱います。DynamoDB の `LOCK` や操作情報を手作業で消して再実行しないでください。

公開中の版より大きい semver かどうかは申請（#9）で検査し、下書きの保存では検査しません。公開中の版を補助ストアに保持するため、AWS が新しい下書きを返している間も「下書きあり」と「公開中」を併記します。申請は後述の #9、公開承認は #10 が担当します。A2A・MCP サーバー・Skill の下書きは次の節で説明します。

## A2A・MCP サーバー・Skill の登録（#8）

下書きの `POST /api/records` と `PATCH /api/records/{recordId}` は、本文の `recordType`（`AGENT` / `MCP` / `SKILL`）と `descriptorType` で種別を切り替えます。どちらも省略すると custom Agent（#7）です。`recordType` だけを指定すると、その種別の既定の descriptor になります。SPA は「下書きを作成」の画面で種別と descriptor を選びます。A2A は独立した種別ではなく、Agent の descriptor の切り替えです。入力は、フォームの各項目、または「JSON で入力」に下書きの JSON を貼り付けて行います。

ポータルは、種別ごとに AWS が受理する primary descriptor を1個だけ組み立てます（[対応する descriptor](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-supported-record-types.html)）。これ以外の組合せ（例: MCP に `a2aAgentCard`）は、AWS へ送る前に日本語のエラー（400）にします。descriptor の種類の値は `backend/src/registry_portal/domain/catalog.py` と `frontend/src/drafts/descriptors.ts` の1か所ずつに定義し、種別との対応や入力項目は `domain/descriptors.py` と `frontend/src/drafts/descriptors.ts` に置きます。descriptor の種類ごとの検査・AWS に送る内容・読み取り・管理情報に置く項目は、`domain/descriptors.py` の `_HANDLERS` の1か所にまとめます。変更時は両側を揃えます。

| 種別 | descriptor | 入力項目（共通項目を除く） | AWS に送る内容 |
| --- | --- | --- | --- |
| `AGENT` | `custom` | 目的、認証方式 | `descriptors.custom.data`（#7 と同じ） |
| `AGENT` | `a2aAgentCard` | `schemaVersion`（`0.3`）、`definition`（Agent Card の JSON） | `a2aAgentCard.data` と `dataSchemaVersion` |
| `MCP` | `mcpServer` | `schemaVersion`（server 定義の版）、`definition`（server.json）、`toolsSchemaVersion`（tools 定義の版）、`toolsDefinition` | `mcpServer.data` / `dataSchemaVersion` と `mcpServer.additionalData.tools.data` / `dataSchemaVersion` |
| `SKILL` | `agentSkillsDefinition` | `schemaVersion`（`0.1.0`）、リポジトリの URL と種類、固定した commit と version（少なくとも一方）、digest（任意）、`skillMd`（SKILL.md） | `agentSkillsDefinition.data`（`repository`）、`dataSchemaVersion`、`additionalData.skillMd.data` |

共通項目は、名前、表示名、説明、semver の版、所有チーム、environment、データ分類、連絡先、利用条件、接続先の参照です。

### 検証

- schema version は、descriptor ごとに許可する値が異なります（`domain/descriptors.py`）。許可外は「schema version は … のいずれかを指定してください」の 400 です。許可値は AWS の公式資料「[対応する descriptor](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-supported-record-types.html)」に記載の `dataSchemaVersion` に従います。
  - `a2aAgentCard`: `0.3`
  - `mcpServer`（server 定義）: `2025-12-11`、`2025-10-17`、`2025-10-11`、`2025-09-29`、`2025-09-16`、`2025-07-09`
  - `mcpServer`（tools 定義）: `2025-11-25`、`2025-06-18`、`2025-03-26`、`2024-11-05`
  - `agentSkillsDefinition`: `0.1.0`
  - 許可値は MCP や A2A の最新版ではなく、AWS が受け付ける版に合わせます。たとえば MCP の現行プロトコル版 2026-07-28 は、AWS が tools 定義の版としてまだ受け付けていないため許可しません（2026-11-07 時点で確認）。
  - AWS が新しい版を追加したら、`domain/descriptors.py` と `frontend/src/drafts/descriptors.ts` の許可値を同時に更新します。
  - 各仕様の最新版は、AWS の許可値より新しいことがあります（2026-11-07 に確認）。ポータルは AWS が受理する版だけを許可します。
    - A2A: 最新は [1.0](https://a2a-protocol.org/latest/specification/)（リリース v1.0.1）。1.0 の Agent Card では `url` と `protocolVersion` が `supportedInterfaces` の中に移ったため、AWS が検証する 0.3 の `AgentCard` には適合しません。1.0 の Agent Card は 0.3 の形に直して登録してください。AWS の資料は仕様のリンク先を `latest` にしていますが、検証に使う JSON Schema は v0.3.0 です。
    - MCP: 最新の仕様は [2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/) です。AWS の tools 定義は 2025-11-25 までなので、ツールの必須項目は 2025-11-25 の Tool に従います。server.json の最新 schema は、AWS の許可値と同じ 2025-12-11 です（2025-08-26 の schema は AWS の許可値にありません）。
- JSON の定義は JSON として読めること、必須項目があることを確認します。必須項目は各仕様の JSON Schema の `required` に従います。
  - Agent Card: [A2A 0.3 の AgentCard](https://a2a-protocol.org/v0.3.0/specification/)（[JSON Schema](https://raw.githubusercontent.com/a2aproject/A2A/v0.3.0/specification/json/a2a.json) の `AgentCard`）の `name`・`description`・`version`・`protocolVersion`・`url`・`capabilities`・`defaultInputModes`・`defaultOutputModes`・`skills`。AWS は `#/definitions/AgentCard` で検証します。
  - server 定義: [server.json の JSON Schema](https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json) の `name`・`description`・`version`。
  - tools 定義: [MCP の Tool](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) の `name` と `inputSchema`。`inputSchema` のルートには `type: "object"` が必要です（許可する tools の schema version のすべてで必須）。ツールを1個以上必須とするのはポータルの規則です。
  - Skill の `repository`: AWS の skill definition（[対応する descriptor](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-supported-record-types.html) の `Repository`）の `url` と `source`。
  - SKILL.md は先頭の frontmatter に `name` と `description` が必要です（値は同じ行か、YAML のブロック形式で次の行以降のインデントした行に書けます）。
- JSON は空白を取り除いた形で保存します。定義の文字数は、補助ストアの1項目の上限（400 KB）に収めるため、Agent Card と server 定義と tools 定義を 24,000 文字、SKILL.md を 32,000 文字までとします（AWS の上限は 102,400 文字。descriptor の `data` は 1〜102,400 文字で、[A2aAgentCardDescriptor](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-agentregistry-registryrecord-a2aagentcarddescriptor.html) や [CustomDescriptor](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-agentregistry-registryrecord-customdescriptor.html) の `Data` に記載されています。補助ストアの操作の入力と結果の2か所に保存するため、それより小さい上限にしています）。
- `environment` は `dev` のみ許可し、省略すると `dev` です。`dataClassification` は `public` / `private` のみで、`confidential` 相当は 400 です。値の一覧は #7 と同じ定義（`DATA_CLASSIFICATIONS`）を使い、別に持ちません。SPA の「JSON で入力」でも、送る前に同じ一覧（`DATA_CLASSIFICATIONS`・`DRAFT_ENVIRONMENTS`）で検証し、サーバーと同じ日本語で示します。
- `isA2A` は入力できません（指定すると未知の項目として 400）。descriptor が `a2aAgentCard` の Agent は `true`、他の Agent は `false` を保存し、descriptor を切り替えて更新すると `isA2A` も更新します。MCP と Skill には `isA2A` を付けません（schema の `AGENT` にだけ追加するため。ADR-0006）。
- Skill は、リポジトリ URL（`https://`、資格情報とクエリを含まない）とリポジトリの種類を必須とします。配布物は、固定した commit（40 または 64 文字の小文字 16 進数）と固定した version の少なくとも一方で固定します（PRD のストーリー 19「固定した commit / version」）。version は数字、または `v` と数字で始まる特定の版（例 `1.2.0`、`v1.2.0`）だけを受け付け、`latest`・`main`・`release-2025` のような名前や、`^1.2`・`1.x` などの範囲指定は受け付けません。digest は取得できれば `sha256:` + 64 文字の小文字 16 進数で記録します。これらの形式は仕様に明記がないため、設計書「秘密情報と外部配布物」の趣旨に沿ってポータルが決めた規則です。AWS は外部配布物が実在することも安全であることも検査しません。ポータルも実ファイルは取得せず、digest の一致は確認しません。

### 秘密情報

定義、metadata、SKILL.md、フォームの文字列に、API キー・JWT・パスワードなどの値らしき文字列があれば、保存せずに 400 で**拒否**します（警告にとどめません）。検出するのは、JWT、AWS アクセスキー、秘密鍵、`Bearer` の値、GitHub・Slack 等のトークン、URL に埋め込んだ資格情報とクエリの資格情報、`password=値` のような代入（値が4文字以上。`password=hunter2` のような短い値も対象）、JSON のキーが `password`・`secret`・`apiKey`・`token`・`authorization` などで値が参照でない文字列です。`${NAME}`・`<…>`・`arn:`・`ssm:`・`secretsmanager:` で始まる値など、保管先への参照は許します。`string` などの型名、`****` のような伏せ字、`required` のような状態を表す語も値とみなしません。MCP の `isSecret: true` のように値を持たない宣言も許します。エラーには場所と種類だけを含め、値は応答にもログにも出しません。検出は正規表現による推定で、漏れと誤検出があり得ます。誤検出された場合は値を参照に置き換えてください。

### 保存先

- 連絡先・利用条件・接続先の参照は、すべての種別で補助ストアの管理情報（`MANAGEMENT`）に保存します。custom descriptor ではこれらも descriptor の中身（正本）に入れ、管理情報は写しです。AWS が検証する他の descriptor にはこれらを入れる項目がないため、管理情報を正本とします。
- Skill の固定した commit・version・digest は、AWS の skill definition（0.1.0）に専用の項目がないため、管理情報（`skillCommit`・`skillVersion`・`skillDigest`）に保存します。リポジトリの URL と種類は descriptor の `repository` に入れます。内容ハッシュに管理情報を含めるため、これらを他の人が変えていれば更新は 409 になります。
- 判断の理由は [ADR-0009](../../docs/adr/0009-non-custom-descriptor-supplement-source.md) を参照してください。

### 更新と結果の確認

- 更新では、descriptor の種類ごとに `data`・`dataSchemaVersion`・`additionalData` を `optionalValue` で個別に更新します。種別は作成後に変更できません。Agent だけ、custom と `a2aAgentCard` を切り替えられます。このとき新しい descriptor を設定し、古い descriptor を空のオブジェクトで削除して、primary descriptor を1個に保ちます。
- 結果の確認（`_verify`）は、AWS が受理後に補う項目（descriptor の `source` など）と、JSON の空白・キーの並びの違いを無視し、送った内容がすべて含まれていれば成功とします。primary descriptor が1個でなければ成功としません。
- 下書きの一覧は `AGENT`・`MCP`・`SKILL` のレコードを対象にし、各項目に `recordType` を含めます。

### 実環境で確認が必要な項目

Stubber は実サービスモデルの形だけを検証します。次の項目は実 AWS で確認していません。デプロイ後に確認してください。

- AWS が受理後に descriptor を整形・補完して返す形（上の `_verify` は、補われる項目を許す設計）。
- `agentSkillsDefinition` の `additionalData.skillMd` に `dataSchemaVersion` が必要か。公式資料に記載がないため送りません。
- descriptor を切り替える更新（古い descriptor の削除と新しい descriptor の設定を1回の更新で行うこと）が成功するか。
- MCP と Skill のレコードに `isA2A` を付けなくても custom metadata の schema に適合するか。
- Agent Card の必須項目の判定が AWS の `#/definitions/AgentCard` と一致するか。ポータルの検査は A2A の必須項目に限っています。

### テスト

- Seam 1: `backend/tests/test_record_descriptors.py`（種別ごとの payload・補助ストア）、`test_record_validation.py`（schema version・組合せ・Skill の固定情報・環境とデータ分類・秘密情報）、`test_record_updates.py`（descriptor 切替と `isA2A`、結果確認、読み取り、一覧）。Stubber で AWS の入力モデルを検証します。
- Seam 2: `frontend/src/tests/recordDrafts.test.ts`（種別の選択、descriptor ごとの入力項目、Skill の commit と version の任意入力、JSON 入力と environment・データ分類の検証、送信する本文、サーバーの日本語エラーの表示）。

`SUPPLEMENT_TABLE_NAME` は下書き操作では必須です。未設定でもカタログは提供し、下書きの API だけを 503 `DRAFTS_NOT_CONFIGURED` にします。既存の `pk` / `sk` 文字列キーの補助テーブルを使い、テーブルの TTL を属性 `expiresAt` で有効にしてください（テーブルの定義は #5 の IaC が持ちます）。TTL が無効でも動作は変わりませんが、確定した操作の項目が削除されずに残ります。

IAM は [下書き用 Terraform モジュール](infrastructure/modules/management_drafts/main.tf) が定義します。このモジュールは既存の管理 IAM ロール名、既存 Registry ARN、補助テーブル ARN を受け取り、Registry レコードの作成・更新・取得・一覧・申請、Registry 設定の読み取りと、対象テーブルの GetItem / PutItem / DeleteItem だけを追加します。DynamoDB のトランザクションは含まれる各操作で認可されます。Registry 本体の変更、schema、決裁、物理削除の権限は追加しません。Registry とテーブルの ARN はリージョンを限定しません。

既存の管理 Lambda のロールへは、ルートモジュール [`infrastructure/management`](infrastructure/management/main.tf) で適用します。CI は `terraform fmt` と `validate` だけを行い、AWS への反映は Admin が手元で行います（ADR-0004）。#5 の IaC が管理 Lambda のロールを定義したら、同じモジュールをその IaC から呼び出し、このルートモジュールの state から取り除いてください。

```bash
cd applications/registry_portal/infrastructure/management
cp terraform.tfvars.example terraform.tfvars   # 値を環境に合わせる。コミットしない
terraform init -backend-config="bucket=<state バケット>" -backend-config="key=registry-portal/management-drafts.tfstate" -backend-config="region=<リージョン>"
terraform plan
terraform apply
```

API Gateway の CORS で `PATCH` と `Idempotency-Key` ヘッダーも許可してください。

作成と更新の AWS payload は別々に変換します。更新では `displayName` / `description` / `customMetadata` / `descriptors` と、custom 内部の `data` に `optionalValue` を使い、`recordVersion` は直接渡します。実サービスモデルの Stubber と moto の handler テストで、payload、排他、冪等性、他チーム拒否、補助情報の変更を含む409、AWS後の補助ストア障害を検証しています。SPA は `fetch` 境界で検証します。

公式資料は [CreateRegistryRecord](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_CreateRegistryRecord.html)、[UpdateRegistryRecord](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_UpdateRegistryRecord.html)、[GetRegistryRecord](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_GetRegistryRecord.html)、[版のライフサイクル](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-record-lifecycle.html)、[Registry IAM](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-iam-permissions.html)、[DynamoDB トランザクションの認可](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html) を参照してください。実 AWS への書き込みはこの実装検証では行っていません。デプロイ後に実環境で非同期状態・公開中の版と下書き・IAM 境界を確認してください。


## 下書きの申請（#9）

`POST /api/records/{recordId}/submit` は、所属チームが所有する保存済みの `DRAFT` を申請します。Publisher 以外、所属チームなし、他チームのレコードは AWS を呼ぶ前に403で拒否します。`REJECTED` は編集して保存し、`DRAFT` に戻してから再申請します。

本文の3項目は必須です。申請は下書きを保存する操作を兼ねません。

```json
{
  "expectedContentHash": "取得した下書きの64桁の内容ハッシュ",
  "reason": "申請理由",
  "relatedReferences": [{"recordId": "skillABC1234", "recordVersion": "1.2.3"}]
}
```

- `reason` は前後の空白を除いた1〜4096文字です。秘密情報らしき値は保存せず400にします。
- `relatedReferences` は Agent のみ指定でき、最大20件です。MCP・Skill は空の配列を指定します。重複と不正な ID・semver は400にします。
- `Idempotency-Key` は任意の1〜128文字の英数字・`_`・`-` です。SPA は申請フォームごとに生成し、同じ申請の再送では維持します。同じキーの異なる本文は409です。確定した失敗の後は新しいキーで再実行します。省略時は本文・主体・最終編集日時から操作 ID を導出します。

申請は既存のレコード単位ロックを取得し、管理 API の `GetRegistryRecord` で AWS の現物を読みます。状態が `DRAFT` でないか、取得時の内容ハッシュと異なれば409です。Agent は metadata の `isA2A` が真偽値で、descriptor と一致することも検査します。

公開中の版と参照先は、発見用 JWT の `BatchGetDiscoverableRegistryRecord` で確認します。`recordVersion` は semver の順序で公開中の版より大きい必要があります。数値の prerelease 識別子も数値として比較します。補助ストアに記録済みの公開版も下限とし、発見 API の反映遅延で版を戻しません。却下された番号が公開されていなければ、編集後の再申請で同じ番号を使えます。AWS がビルドメタデータの `+` を受け付けないため、既存の下書き入力と同様に `+build` は許可しません。関連参照は指定した ID と版が公開承認済みの場合だけ受け付けます。AWS や補助ストアを読めない場合は502にし、未公開や0件とは区別します。[公開版と管理上の最新版](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-record-lifecycle.html)

毎回 `GetRegistry` で自動承認設定も確認します。`approvalConfiguration.autoApprovalRules` が省略または空なら手動承認です。ルールがあれば503 `APPROVAL_CONFIGURATION_CHANGED` とし、申請を止めて管理者への連絡を表示します。AWS へは書き込んでいないため、SPA はこの503を結果不明として扱わず、フォームを再開して次の申請を新しいキーで行います。アプリが設定を変更することはありません。[GetRegistry](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_GetRegistry.html)、[自動承認設定](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_ApprovalConfiguration.html)

### 固定する申請内容

補助テーブルの `pk` は管理情報と共通、`sk` は `SUBMISSION#{submissionId}` です。スナップショットと操作の実行制御を同一の DynamoDB トランザクションで保存してから、`SubmitRegistryRecordForApproval` を呼びます。スナップショットは条件付きで新規作成し、上書きせず、TTL を付けません。

| 属性 | 内容 |
|---|---|
| `submissionId` | 申請の操作 ID |
| `submittedBy`・`lastEditedBy` | 検証済みの `{issuer, subject}`。申請者と最終編集者を別々に保存 |
| `submittedAt`・`reason` | 申請日時と理由 |
| `record` | AWS 現物の ID・名前・表示名・説明・種別・版・descriptor・metadata・申請前の更新日時 |
| `relatedReferences` | 申請時に確認した ID と版の配列 |
| `supplement` | 所有チーム、連絡先など、Skill の commit・version・digest を含む補助情報 |
| `contact`・`termsOfUse`・`connectionReference` | #10 が公開版スナップショットへ写す値。custom Agent は現物の descriptor、その他の種別は管理情報から取得。欠けていれば空文字で補わず400 |
| `contentHash` | 既存の下書きと同じ、AWS の内容と補助情報の SHA-256 |
| `snapshotHash` | この属性自身と DynamoDB の `pk`・`sk` を除く全属性を canonical JSON にした SHA-256 |

canonical JSON はキーを並べ、区切りを `,` と `:` に固定し、Unicode をエスケープせず UTF-8 にします。AWS の更新日時は ISO 8601 の文字列にします。申請 API 自身が AWS の状態と更新日時を変えるため、`record.updatedAt` は申請前の証跡です。#10 は受け取った `snapshotHash` を保存済みスナップショットと照合し、現物の比較には状態と `contentHash` を使います。

### 申請結果と編集中の無効化

AWS の申請 API は202を返します。ポータルは続けて現物を再取得し、内容が一致する `PENDING_APPROVAL` を確認した場合だけ200 `SUCCEEDED` とします。応答は既存の下書き操作と同じ形に `submissionId` と `snapshotHash` を加えます。`MANAGEMENT.activeSubmissionId` はこの確定時にだけ設定します。公開承認と `PUBLISHED#{recordVersion}` の保存は #10 が担当します。[SubmitRegistryRecordForApproval](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_SubmitRegistryRecordForApproval.html)

タイムアウト、AWS の5xx、AWS 成功後の補助ストア障害、再取得でまだ `DRAFT` が見える場合は202 `VERIFYING` を返します。ロックは期限で解除せず、別の書き込みを409で止めます。`GET /api/operations/{operationId}` は現物を再取得して結果を確定します。スナップショットの保存後に申請 API の受け付けを確認できていない操作（`approvalRequested` がない）で、現物が内容の一致する `DRAFT` のままなら、申請 API を呼び直します。先の呼び出しが届いていた可能性があるため、呼び直しのエラーは失敗と確定せず202のまま次の照合に委ねます。受け付けを確認済みの操作は呼び直しません。AWS が入力不備や混雑で処理せず拒否したと確定した場合は失敗を記録し、ロックを解除します。申請前の確認に失敗した場合も AWS へ申請せずロックを解除します。

承認待ちや却下後の編集が AWS で `DRAFT` に戻ったことを確認すると、`activeSubmissionId` を消し、`SUBMISSION#{元の申請ID}#INVALIDATED` を追記します。元のスナップショットは残します。#10 はレコード共通ロックに加え、申請が現在の `activeSubmissionId` と一致し、無効化記録がないことを確認する必要があります。`DraftStore.get_submission` はスナップショットに無効化の有無（`invalidated`）を付けて返すので、#10 はこの入口から読みます。

Seam 1 の Stubber と moto で、保存の順序、認可、公開版との semver 比較、関連参照、`isA2A`、自動承認、固定内容とハッシュ、補助情報が欠けた下書きの拒否、編集時の無効化と `get_submission` の `invalidated`、同じ未公開番号の再申請、AWS の結果不明、スナップショット保存後に止まった申請の再開、AWS 成功後の補助ストア障害を検証します。Seam 2 の Vitest で、申請の `Idempotency-Key`、409の再取得案内、503 `APPROVAL_CONFIGURATION_CHANGED` の表示を確認します。実 AWS への書き込みは行っていません。デプロイ後は実環境で申請時の非同期状態、公開中の版との併存、IAM 境界を確認してください。
