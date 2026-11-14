# Registry ポータル

AWS Agent Registry を唯一の正本として、Agent・MCP サーバー・Skill の登録、公開承認、発見を扱う管理ポータルです。用語は [GLOSSARY.md](../../GLOSSARY.md)、設計は [MVP1 設計](../../plan/aws-agent-registry-mvp1-design-ja.md) と `docs/adr/` に従います。

既存の Control Plane とはコードを共有しない、独立したアプリです。

## 構成

| パス | 内容 |
|---|---|
| `backend/src/registry_portal/handlers/management.py` | 管理 handler の Lambda エントリーポイント。認証設定がなければ初期化に失敗する |
| `backend/src/registry_portal/handlers/curation.py` | 決裁 handler の Lambda エントリーポイント。認証設定または補助テーブルがなければ初期化に失敗する |
| `backend/src/registry_portal/handlers/events.py` | イベント処理 handler の Lambda エントリーポイント（#15）。Registry の ARN または補助テーブルがなければ初期化に失敗する |
| `backend/src/registry_portal/app.py` | API Gateway HTTP API（payload format 2.0）のイベントを受け取り、route を振り分ける |
| `backend/src/registry_portal/auth.py` | 認可。検証済みの claims から主体・業務ロール・所属チームを導出する |
| `backend/src/registry_portal/errors.py` | 共通エラー DTO と日本語メッセージ |
| `backend/src/registry_portal/catalog.py` | カタログの一覧・検索・詳細 API。入力の検証、DTO への変換、サービス層の失敗の API エラーへの変換 |
| `backend/src/registry_portal/domain/catalog.py` | カタログ項目のモデル、種別（`AGENT`・`MCP`・`SKILL`）とデータ分類（`public`・`private`）、A2A を指定できる種別の規則。SPA の `CatalogRecordType`・`DataClassification` と値を揃える |
| `backend/src/registry_portal/services/discovery.py` | 発見アダプター。発見用 M2M トークンの取得・キャッシュと、JWT 認可の Registry の発見 API 呼び出し |
| `backend/src/registry_portal/services/catalog_supplement.py` | 補助テーブルから所有チームの正本と公開版のスナップショットを読む |
| `backend/src/registry_portal/services/http.py` | 標準ライブラリによる HTTP トランスポート。テストではフェイクに差し替える |
| `backend/src/registry_portal/operations.py` | 下書きと決裁 handler の操作に共通する API 層の補助（AWS のエラーの分類、`Idempotency-Key` とレコード ID の検査、操作の応答と request ID の記録） |
| `backend/src/registry_portal/drafts.py` | 下書きの作成・編集・一覧・申請・操作の照合 API。排他・冪等性、申請前の検査と申請 API の呼び出し、AWS の失敗の API エラーへの変換 |
| `backend/src/registry_portal/deprecation.py` | Publisher の廃止依頼（管理 handler）と Curator の見送り（決裁 handler）。所有チームの検査、ロックの確認、依頼の保存・解決と操作記録の追記を担う |
| `backend/src/registry_portal/curation_reads.py` | 審査・決裁・公開管理・廃止依頼が共通で使う Registry と補助ストアの読み取り、レコードの要約、例外（502・404）、Curator の認可 |
| `backend/src/registry_portal/review_queue.py` | 審査待ちの一覧（申請と未解決の廃止依頼）と審査の詳細 API |
| `backend/src/registry_portal/publication.py` | Curator の公開管理の照会 API。現在の状態・公開中の版・未解決の廃止依頼・処理中の操作と、実行できる操作（できない理由）を返す |
| `backend/src/registry_portal/curation.py` | Curator の決裁（公開承認・却下）と公開停止・廃止の実行。対象（申請スナップショットか公開中の現物か）ごとの照合と、共通の手順（意図の記録→AWS→再取得→確定）を担う。決裁 handler と管理 handler の操作照会（`app.py` が操作の種別で振り分ける）の両方から使う |
| `backend/src/registry_portal/domain/drafts.py` | 下書きのフォーム入力・管理情報のモデル、AWS の作成用・更新用 payload への変換、状態と environment の値。SPA の `frontend/src/drafts/api.ts` と値を揃える |
| `backend/src/registry_portal/domain/submissions.py` | 申請入力、semver の比較、`isA2A`・公開中の版・関連参照の参照先の検査、申請スナップショットの構築と、保存済みスナップショットからのハッシュの再計算 |
| `backend/src/registry_portal/domain/curation.py` | 決裁入力（スナップショットのハッシュと理由）・公開停止と廃止の入力と理由の検査、決裁・公開停止・廃止の前提の規則、自己承認の禁止、操作ごとの AWS の遷移先、審査待ちの項目の種別、公開版スナップショット（`PUBLISHED#版`）への書き出しと差分用の読み取り |
| `backend/src/registry_portal/domain/deprecation.py` | 廃止依頼のモデル（状態・終わり方・理由の検査、保存形式と API の形）。SPA の `frontend/src/drafts/api.ts` と値を揃える |
| `backend/src/registry_portal/notification_events.py` | Registry の EventBridge イベントから通知を作るイベント処理。Registry の再取得、操作記録との照合（ポータル外の変更の判定）、宛先グループの決定、event ID と状態変化での重複除去 |
| `backend/src/registry_portal/notifications.py` | 自分宛ての通知の一覧・未読件数と既読位置の更新の API。業務ロールと所属チームから宛先グループを決める |
| `backend/src/registry_portal/domain/notifications.py` | 通知のモデル（種別・宛先グループ・本文・既読位置の形式・保持期間）と、Registry の状態変化を操作記録と照合する規則。SPA の `frontend/src/notifications/api.ts` と値を揃える |
| `backend/src/registry_portal/services/notification_store.py` | 補助テーブルの通知・重複除去の印・処理済みのイベント・既読位置の読み書き |
| `backend/src/registry_portal/services/supplement_store.py` | 補助テーブルの項目（管理情報・ロック・操作・操作記録・申請スナップショット `SUBMISSION#`・無効化記録・公開版スナップショット・廃止依頼）の読み書き |
| `infrastructure/` | 下書きの IAM モジュール（`modules/management_drafts`）、決裁 Lambda・実行ロール・審査と決裁の route のモジュール（`modules/decision`）、既存ロールへ適用するルートモジュール（`management`）と決裁 Lambda 用のルートモジュール（`decision`） |
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

`registry-publisher` を持てば、`team-*` がなくても業務ロールは Publisher と表示します。下書きの作成・編集・申請・廃止依頼では、対象の所有チームが所属チームに含まれることを別途検査します（いずれも実装済み。設計書「ロールと所有」）。審査待ちの閲覧と決裁（公開承認・却下）、公開停止・廃止・廃止依頼の見送り・公開管理の照会は Curator の業務ロールだけが行え、チームでは絞り込みません（実装済み。設計書「ロールと所有」）。

メールアドレスや `x-user-email` などのリクエストヘッダーは、主体や権限の判定に使いません。

認証できないトークン（トークンなし、ID トークン、別 issuer、`sub` なし）は 401 `UNAUTHENTICATED` で拒否します。署名は正しいが人間用 SPA client 以外に発行されたトークン（別 client、発見用 M2M client）は、ログインし直しても解決しないため 403 `FORBIDDEN` で拒否します。SPA が「もう一度ログインする」を表示するのは、401 とログインの失敗のときだけです。API Gateway の JWT authorizer が返す 401（共通 DTO ではない）も、同じ認証エラーとして扱います（設計書「401 と 403 の使い分け」）。

### API

| API | 内容 |
|---|---|
| `GET /api/me` | 自分の主体、業務ロール、所属チーム |
| `GET /api/catalog?type=AGENT\|MCP\|SKILL[&a2a=true][&nextToken=...]` | カタログ項目（公開承認済みの Registry レコード）を種別ごとに1ページ（最大50件）返す。a2a は Agent の一覧のみ指定可。ログインした全員（Viewer）が呼び出せる |
| `POST /api/catalog/search` | 自然言語・キーワード（必須）と絞り込み条件で検索し、関連度順に最大20件を返す。Viewer が呼び出せる |
| `GET /api/catalog/{recordId}` | 指定したレコード ID の公開中の版、所有チーム、その版の連絡先・利用条件・接続先の参照・関連参照を返す。Viewer が呼び出せる |
| `POST /api/records/{recordId}/deprecation-requests` | 廃止依頼。対象の所有チームを所属チームに持つ Publisher のみ（詳細は「公開停止・廃止・廃止依頼」の節） |
| `GET /api/reviews` | 審査待ちの一覧（AWS の承認待ちと廃止依頼に、申請の補助情報を結合）。Curator のみ（詳細は #10 の節） |
| `POST /api/records/{recordId}/suspend` | 公開停止（却下で発見できなくする。廃止依頼は閉じない）。Curator のみ（決裁 handler。詳細は #13 の節） |
| `POST /api/records/{recordId}/deprecate` | 廃止（元に戻せない DEPRECATED）。理由の入力と確認（confirm）が必須。Curator のみ（決裁 handler。詳細は #13 の節） |
| `GET /api/records/{recordId}/publication` | 公開管理の照会（状態・公開中の版・未解決の廃止依頼・実行できる操作）。Curator のみ（決裁 handler。詳細は #13 の節） |
| `POST /api/records/{recordId}/deprecation-requests/decline` | 廃止依頼の見送り（理由が必須。レコードの状態は変えない）。Curator のみ（決裁 handler。詳細は #13 の節） |
| `GET /api/notifications` | 自分宛ての通知（新しい順に最大50件）・未読件数・既読位置。宛先グループを持たない Viewer は0件（詳細は #15 の節） |
| `POST /api/notifications/read` | 既読位置の更新。本文は `{ "readUpTo": 通知の position }`（詳細は #15 の節） |

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
| `record` | M（#10 以降） | 公開承認した定義と metadata（申請スナップショットの `record`）。カタログ詳細は読まず、次回審査の差分に使います |
| `approvedBy` / `approvedAt` | M / S（#10 以降） | 公開承認した Curator（`issuer` と `subject`）と日時。記録がない版では審査画面に「不明」と表示します |

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
| `SUPPLEMENT_TABLE_NAME` | （任意。下書きの管理には必須）Registry と同じリージョンにある補助 DynamoDB テーブル名。所有チームの正本と公開版のスナップショットを読み、下書きの管理情報・ロック・操作を保存する。カタログだけなら `dynamodb:GetItem`、下書きには「custom Agent の下書き」の IAM が必要。未設定なら下書きの API は 503。決裁 handler（`handlers/curation.py`）はこれが必須で、未設定なら起動エラー |
| `A2A_SEARCH_ENABLED` | （任意）`true` で A2A の絞り込み検索を有効にする。既定は無効。`isA2A` の準備が終わるまで有効にしない（「A2A 検索を有効にする」を参照）。`true`・`false` 以外は起動エラー |

イベント処理 handler（`handlers/events.py`、#15）は `REGISTRY_ARN` と `SUPPLEMENT_TABLE_NAME`（必須）だけを使い、認証・発見の設定は読みません。

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

どちらでも確定できない操作（補助ストア障害が続く場合など）を強制解除する回復手順は、[Admin の回復操作（#12）](#回復操作12)で行います。DynamoDB の `LOCK` や操作情報を手作業で消して再実行しないでください。

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

既存の管理 Lambda のロールへは、ルートモジュール [`infrastructure/management`](infrastructure/management/main.tf) で適用します。このルートモジュールは、ポータルが追加した管理 handler の route（履歴・廃止依頼）も [route 用モジュール](infrastructure/modules/management_routes/main.tf) で割り当てます（「公開停止・廃止・廃止依頼」の「IAM と route」）。CI は `terraform fmt` と `validate` だけを行い、AWS への反映は Admin が手元で行います（ADR-0004）。#5 の IaC が管理 Lambda のロールを定義したら、同じモジュールをその IaC から呼び出し、このルートモジュールの state から取り除いてください。

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
- `relatedReferences` は Agent のみ指定でき、最大20件です。MCP・Skill は空の配列を指定します。参照先は Skill または MCP サーバーのカタログ項目に限り、申請するレコード自身は指定できません（設計書「関連参照」）。重複と不正な ID・semver は400にします。
- `Idempotency-Key` は必須で、1〜128文字の英数字・`_`・`-` です。省略すると400です。SPA は申請フォームごとに生成し、同じ申請の再送では維持します。同じキーの異なる本文は409です。確定した失敗の後は新しいキーで再実行します。

申請は既存のレコード単位ロックを取得し、管理 API の `GetRegistryRecord` で AWS の現物を読みます。状態が `DRAFT` でないか、取得時の内容ハッシュと異なれば409です。Agent は metadata の `isA2A` が真偽値で、descriptor と一致することも検査します。

公開中の版と参照先は、発見用 JWT の `BatchGetDiscoverableRegistryRecord` で確認します。`recordVersion` は semver の順序で公開中の版より大きい必要があります。数値の prerelease 識別子も数値として比較します。補助ストアに記録済みの公開版も下限とし、発見 API の反映遅延で版を戻しません。却下された番号が公開されていなければ、編集後の再申請で同じ番号を使えます。AWS がビルドメタデータの `+` を受け付けないため、既存の下書き入力と同様に `+build` は許可しません。関連参照は指定した ID と版が公開承認済みの場合だけ受け付けます。廃止済み（`DEPRECATED`）など公開承認済みの版を持たない参照先は、カタログ項目ではないとして400にします。AWS や補助ストアを読めない場合は502にし、未公開や0件とは区別します。[公開版と管理上の最新版](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-record-lifecycle.html)

毎回 `GetRegistry` で自動承認設定も確認します。照合で申請 API を呼び直す前にも確認し直します（後述）。`approvalConfiguration.autoApprovalRules` が省略または空なら手動承認です。ルールがあれば503 `APPROVAL_CONFIGURATION_CHANGED` とし、申請を止めて管理者への連絡を表示します。AWS へは書き込んでいないため、SPA はこの503を結果不明として扱わず、フォームを再開して次の申請を新しいキーで行います。アプリが設定を変更することはありません。[GetRegistry](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_GetRegistry.html)、[自動承認設定](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_ApprovalConfiguration.html)

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

AWS の申請 API は202を返します。ポータルは続けて現物を再取得し、内容が一致する `PENDING_APPROVAL` を確認した場合に200 `SUCCEEDED` とします。照合より先に審査で決裁されて `APPROVED`・`REJECTED` になっていた場合も、申請は受け付けられているため `SUCCEEDED` とします。申請 API が `ConflictException` を返した場合は、申請前の検査の後で状態が変わったものとして操作を失敗に確定し、409 `CONTENT_CONFLICT` を返します。応答は既存の下書き操作と同じ形に `submissionId` と `snapshotHash` を加えます。`MANAGEMENT.activeSubmissionId` はこの確定時にだけ設定します。公開承認と `PUBLISHED#{recordVersion}` の保存は #10 が担当します。[SubmitRegistryRecordForApproval](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_SubmitRegistryRecordForApproval.html)

タイムアウト、AWS の5xx、AWS 成功後の補助ストア障害、再取得でまだ `DRAFT` が見える場合は202 `VERIFYING` を返します。ロックは期限で解除せず、別の書き込みを409で止めます。`GET /api/operations/{operationId}` は現物を再取得して、次のように結果を確定します。

| 現物 | 照合の結果 |
|---|---|
| `CREATING`・`UPDATING` | 202 `VERIFYING` のまま |
| 内容がスナップショットと一致しない（ポータル外の編集など） | 申請は成立しないため `FAILED`（`CONTENT_CHANGED`）を確定し、ロックを解除 |
| 内容が一致する `DRAFT`・申請 API の受け付けを確認済み | 反映待ちとして202のまま |
| 内容が一致する `DRAFT`・受け付けを未確認（`submissionAccepted` がない） | 自動承認設定を確認し直してから申請 API を呼び直す。設定が想定外なら呼ばずに `FAILED` を確定。呼び直しのエラーは、先の呼び出しが届いていた可能性があるため失敗と確定せず202のまま |
| 内容が一致する `PENDING_APPROVAL`・`APPROVED`・`REJECTED` | `SUCCEEDED` を確定し、ロックを解除 |
| それ以外（`DEPRECATED` など） | `FAILED` を確定し、ロックを解除 |

照合（読み取りの GET）が申請 API を呼び直すのは、スナップショットの保存後に止まった操作がロックを持ち続けないようにするためです。設計書「同時操作」に、この例外を記載しています。AWS が入力不備や混雑で処理せず拒否したと確定した場合は失敗を記録し、ロックを解除します。申請前の確認に失敗した場合も AWS へ申請せずロックを解除します。

審査待ちや却下後の編集をポータルで保存し、AWS で `DRAFT` に戻ったことを確認すると、`activeSubmissionId` を消し、`SUBMISSION#{元の申請ID}#INVALIDATED` を追記します。元のスナップショットは残します。#10 はレコード共通ロックに加え、申請が現在の `activeSubmissionId` と一致し、無効化記録がないことを確認する必要があります。`SupplementStore.get_submission` はスナップショットに無効化の有無（`invalidated`）を付けて返すので、#10 はこの入口から読みます。無効化記録はポータルでの編集だけが書くため、AWS コンソールなどポータル外の編集では `invalidated` は偽のままです。#10 は決裁の前に現物の `contentHash` をスナップショットの `contentHash` と照合し直し、一致しなければ決裁してはいけません。

Seam 1 の Stubber と moto で、保存の順序、認可、公開版との semver 比較、関連参照、`isA2A`、自動承認、固定内容とハッシュ、補助情報が欠けた下書きの拒否、編集時の無効化と `get_submission` の `invalidated`、同じ未公開番号の再申請、AWS の結果不明、スナップショット保存後に止まった申請の再開と再開前の自動承認設定の再確認、照合前の決裁・ポータル外の編集・廃止でのロック解除、`Idempotency-Key` の必須化、申請 API の `ConflictException` の409、廃止済み・Agent・自身を参照先にした関連参照の拒否、AWS 成功後の補助ストア障害を検証します。Seam 2 の Vitest で、申請の `Idempotency-Key`（確定した失敗の後に作り直す判定は作成と共通）、409の再取得案内、503 `APPROVAL_CONFIGURATION_CHANGED` の表示を確認します。実 AWS への書き込みは行っていません。デプロイ後は実環境で申請時の非同期状態、公開中の版との併存、IAM 境界を確認してください。

## 審査と決裁（#10）

Curator は審査待ちの一覧を見て、申請のスナップショットと公開中の版の差分を確認し、公開承認または却下を行います。決裁は決裁 handler（`handlers/curation.py`）だけで行い、専用の実行ロールには状態遷移（`UpdateRegistryRecordStatus`）と読み取りだけを許可します。descriptor の作成・更新（`CreateRegistryRecord` / `UpdateRegistryRecord`）と申請（`SubmitRegistryRecordForApproval`）の権限は持ちません（設計書「決裁 Lambda」）。クライアントから AWS の `status` を受け取る API は作らず、API ごと（approve / reject）に遷移先を決めます。

| API | 契約 |
| --- | --- |
| `GET /api/reviews` | AWS の承認待ち（`PENDING_APPROVAL`）を正本とする一覧に、申請の補助情報（申請者・申請理由・スナップショットのハッシュ）を結合して返す。`nextToken` をそのまま中継する。Curator のみ |
| `GET /api/reviews/{recordId}` | 審査対象の詳細。申請のスナップショット全体と、差分の比較対象（公開中の版のスナップショット）を返す。審査待ちでなければ404 `PENDING_APPROVAL_NOT_FOUND` |
| `POST /api/reviews/{recordId}/approve` | `{ "snapshotHash": 審査したスナップショットのハッシュ, "reason": 公開承認の理由 }`。理由は必須。`Idempotency-Key` 必須。Curator のみ |
| `POST /api/reviews/{recordId}/reject` | 同じ本文。却下理由は必須。Curator のみ |
| `GET /api/operations/{operationId}` | 既存の操作照会。決裁操作は、その決裁を行った Curator 本人だけが参照できる（下書き操作は従来どおり所有チームの Publisher） |

一覧と詳細の `submissionState` は申請スナップショットの取得状態です。`NONE` はポータル経由の申請がない、`AVAILABLE` は取得できた、`MISSING` は管理情報が申請を指しているのにスナップショットを読めない（保存内容の異常）ことを表し、SPA は「申請なし」と「取得できない」を区別して表示します。詳細の `publishedState` は公開版スナップショットの取得状態で、`NONE` は公開版がない（初回公開）、`MISSING` は公開中の版があるのにスナップショットがない（ADR-0007 の「未取得」）ことを表します。詳細の `snapshotIntact` は、保存済みの申請スナップショットから計算し直したハッシュが申請時の `snapshotHash` と一致するかを示し、偽なら SPA は決裁させません（API も409で拒否します）。

### 決裁の手順と守る規則

決裁は設計書「内容を確認してから決裁する」の手順で行います。AWS の `ListRegistryRecords` は `status` フィルターを受け付けるため、審査待ちの一覧は AWS に問い合わせます（[ListRegistryRecords](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_ListRegistryRecords.html)）。

1. レコード単位のロック（下書きと共通の `LOCK`）を取り、決裁の意図（対象・理由・種別）を `AUDIT#…#INTENT` に記録します。
2. 現物と申請スナップショットを照合し、対象を操作に固定（pin）して保存します。次のすべてが揃わなければ、AWS を呼ばずに409 `CONTENT_CONFLICT` で拒否します。
   - AWS のレコードが `PENDING_APPROVAL` であること。却下済み（再申請が必要）、AWS 処理中（`CREATING` / `UPDATING`）、AWS での作成・更新の失敗（`*_FAILED`）、すでに決裁済みは拒否します
   - 申請が管理情報の `activeSubmissionId` と一致し、無効化記録（`SUBMISSION#…#INVALIDATED`）がないこと
   - 保存済みスナップショットの内容から申請時と同じ方法（canonical JSON の SHA-256）で計算し直したハッシュが、記録された `snapshotHash` と一致すること（保存後の書き換えを検出します）
   - Curator が送った `snapshotHash` が、保存済みスナップショットの `snapshotHash` と一致すること
   - 現物から計算し直した内容ハッシュ（`contentHash`）が、スナップショットの `contentHash` と一致すること
   - 決裁する Curator が申請者でも最終編集者でもないこと（自己承認の禁止。Admin が Curator を兼ねていても同じで、403 `FORBIDDEN`）
3. AWS の [UpdateRegistryRecordStatus](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_UpdateRegistryRecordStatus.html) を `statusReason` = 決裁理由で呼びます。理由は公開承認・却下の両方で必須とし、どちらも記録します（設計書「API」の「理由とスナップショットのハッシュを検査して公開承認する」）。記録した理由をそのまま AWS に送れるよう、どちらも255文字以内に制限します（AWS の `statusReason` が 0〜255 文字であるためです。[サービスモデルの `UpdateRegistryRecordStatusRequestStatusReasonString`](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_UpdateRegistryRecordStatus.html) を参照してください）。
4. 再取得して、期待した状態・版・内容ハッシュが確認できてから成功を記録します。確認できなければ202 `VERIFYING`（結果確認中）とし、ロックを保持してそのレコードへの書き込みを止めます。AWS が処理せず拒否したと確定できる場合（`ValidationException` など）は失敗として確定し、ロックを解除します。混雑（`ThrottlingException`）は429、レコードの状態が変わっていた（`ConflictException`）は409です。AWS が失敗したものを承認済みとは表示しません。

公開承認が確定すると、Curator が確認した内容を公開版スナップショット（ADR-0007）として保存します（キー: `pk` = `REGISTRY#{arn}#RECORD#{recordId}`、`sk` = `PUBLISHED#{recordVersion}`）。`contact`・`termsOfUse`・`connectionReference`・`relatedReferences` は申請スナップショットの値をそのまま写し、カタログ詳細が読み取ります。次回審査の差分に使うため、定義と metadata（`record`）と公開承認した Curator（`approvedBy`・`approvedAt`）も保存します。同時に管理情報の `publishedVersion` を更新し、`activeSubmissionId` を消します。却下は公開版スナップショットを書き換えず、`activeSubmissionId` だけを消します。

### 結果確認中の決裁の確定

`UpdateRegistryRecordStatus` に冪等性の引数がないため、結果不明の決裁を再送では確定しません。確定の経路は2つです。

- **同じ `Idempotency-Key` での再送**（決裁 handler）: 同じキー・同じ本文なら同じ操作として扱い、現物から結果を確定します。AWS の呼び出しまで至らなかった操作は、意図に記録した同じ決裁（種別・理由）を送り直します。先の呼び出しが AWS に届いていた可能性があるため、送り直しのエラーでは失敗と確定しません。同じキーに異なる本文は409です。確定した失敗の後は新しいキーを使います。
- **操作照会 `GET /api/operations/{id}`**（管理 handler）: 決裁した Curator 本人だけが参照できます。AWS を呼び直さず、観測できる状態から確定します（管理 Lambda の実行ロールには決裁の権限がないため）。`APPROVED` / `REJECTED` が見えれば成功として確定し、公開版スナップショットも保存します。`PENDING_APPROVAL` のままなら反映待ちとして202を返します。

公開承認の後、検索（発見 API）は結果整合性のためすぐには反映されないことがあります。SPA は承認の確定後に「検索への反映待ち」を表示します（PRD ストーリー15）。

### 決裁 handler と SPA

決裁 handler は審査と決裁の4つの API と、[Admin の回復操作（#12）](#回復操作12)の2つの API だけを受け持ちます。補助テーブル（`SUPPLEMENT_TABLE_NAME`）がないとどの API も提供できないため、起動エラーにします（カタログを提供し続ける管理 handler とは異なります）。発見用 JWT の設定（`DISCOVERY_*`）は使いません。公開中の版は補助ストアに保持した `publishedVersion` と `PUBLISHED#版` スナップショットから読みます。

SPA の「審査待ち」は Curator（`registry-curator` グループ）だけに表示します。一覧から審査対象を開くと、申請のスナップショット（申請理由・申請者・定義と metadata・連絡先など）と公開中の版との差分（表示名・説明・定義・metadata・連絡先・利用条件・接続先の参照・関連参照）、公開中の版を公開承認した Curator を確認できます。409では最新の申請の再読み込みを促し、結果不明の失敗では同じ `Idempotency-Key` で再試行します。ポータルで管理されていない承認待ち（ポータル外で申請されたもの）は一覧に表示しますが、決裁はできません。

### IAM とルートモジュール

決裁 Lambda と専用の実行ロール、審査と決裁の route は [決裁用 Terraform モジュール](infrastructure/modules/decision/main.tf) が定義します。ポリシー本文は [テンプレート](infrastructure/modules/decision/decision-policy.json.tftpl) に分けており、テスト（`backend/tests/test_decision_permissions.py`）で、状態遷移と読み取り・補助テーブルの操作・ログだけが許可され、descriptor の作成・更新や申請の権限がないことを検査します。ルートモジュール [`infrastructure/decision`](infrastructure/decision/main.tf) から適用します。

```bash
cd applications/registry_portal/infrastructure/decision
cp terraform.tfvars.example terraform.tfvars   # 値を環境に合わせる。コミットしない
terraform init -backend-config="bucket=<state バケット>" -backend-config="key=registry-portal/decision.tfstate" -backend-config="region=<リージョン>"
terraform plan
terraform apply
```

モジュールは、借用する既存 HTTP API（`http_api_id`）に `GET /api/reviews`、`GET /api/reviews/{recordId}`、`POST /api/reviews/{recordId}/approve`、`POST /api/reviews/{recordId}/reject`、`POST /api/records/{recordId}/suspend`、`POST /api/records/{recordId}/deprecate`、`GET /api/records/{recordId}/publication`、`POST /api/records/{recordId}/deprecation-requests/decline`、`GET /api/records/{recordId}/recovery`、`POST /api/records/{recordId}/recovery` の route を作り、決裁 Lambda（payload format 2.0）に統合して既存の JWT authorizer（`jwt_authorizer_id`）を適用します。HTTP API の呼び出し許可（`aws_lambda_permission`）は `/api/reviews` 以下と `/api/records/*/suspend`・`/api/records/*/deprecate`・`/api/records/*/publication`・`/api/records/*/deprecation-requests/decline`・`/api/records/*/recovery` の path に限ります。HTTP API・authorizer・CORS の設定は #5 の IaC が持ちます。CORS で `POST` と `Idempotency-Key` ヘッダーを許可してください。

Seam 1 の Stubber と moto で、認可（Curator 以外の拒否）、AWS 正本の一覧と補助情報の結合、保存済みスナップショットから計算し直したハッシュ・Curator が見たハッシュ・内容ハッシュの照合による409、自己承認の拒否（Admin を含む）、却下済み・処理中の拒否、公開承認・却下の理由の必須化と255文字上限、申請・公開版スナップショットが読めない場合の `MISSING`、決裁操作の照会を決裁した Curator に限ること、`status` の不接受、AWS の拒否・混雑・結果不明、同じキーでの再送と操作照会による確定、AWS 成功後の補助ストア障害、公開版スナップショットと管理情報の保存を検証します。Seam 2 の Vitest で、Curator 以外の非表示、一覧と差分（定義と metadata を含む）の表示、「申請なし」と「取得できない」・「初回公開」と「未取得」の区別、書き換えられたスナップショットの決裁停止、承認・却下の本文とキー、「検索への反映待ち」、409 の再読み込み、結果不明の再試行を確認します。実 AWS への書き込みは行っていません。デプロイ後に実環境で、別ユーザーによる承認までの流れと IAM の境界を確認してください。

## 操作と審査の履歴（#11）

`GET /api/records/{recordId}/history` は、Registry レコードごとの操作と審査の履歴を返します。履歴の正本は補助テーブルの追記記録（`AUDIT#<操作 ID>#INTENT` / `AUDIT#<操作 ID>#RESULT`）だけで、保存した記録から組み立てます。作成の操作はロックをレコード名で取るため、レコード ID が分かった時点（AWS が作成を受け付け、結果確認中として保存するとき）に INTENT（開始時の意図）をレコード単位の追記記録にも写し、結果の確定時に RESULT も写します。そのため作成が結果確認中のまま止まっても、管理情報と同時に履歴へ現れます。INTENT の写しは開始時の意図に揃え、開始後に書き足す `recordId`・`awsRequestId` を含めません。定義の全文（`draft`）は名前のロック側の正本にだけ残し、無期限に残る写しには重ねて保存しません。追記はどちらも上書きを禁止する条件式（`attribute_not_exists`）付きで行い、確定した操作の実行制御項目（TTL 付き）とは違い、追記記録は無期限に保持します（設計書「監査」）。

各項目は、操作 ID・主体（`principal`、issuer と subject の組。GLOSSARY「主体」）・対象のハッシュ（`targetHash`）・開始時刻・結果（成功 / 失敗とエラーコード / 結果確認中）・AWS request ID を含みます。決裁（公開承認・却下）には理由と対象の版も含まれます。対象のハッシュは、編集・申請・決裁では操作の時点の内容ハッシュ（またはスナップショットのハッシュ）、作成では作成した内容の内容ハッシュです。作成の結果が確定するまでは内容ハッシュがないため `null` です（依頼の照合に使う `requestHash` は内容ハッシュではないため使いません）。必須の項目（`operationId`・`kind`・`principal`・`startedAt`・`targetHash`・`state`）は値がなくても `null` として必ず返します。結果が確定していない操作（INTENT だけがあるもの）は、操作照会と同じ `VERIFYING`（結果確認中）で返します。結果の値（`HistoryState`）は `backend/src/registry_portal/domain/drafts.py` に定義し、SPA の `HISTORY_STATES`（`OPERATION_STATES` に `UNKNOWN` を加えたもの）と揃えます。

追記記録は無期限に積み上がるため、Query の1回の応答の上限（1MB）を超えても欠けないよう、`LastEvaluatedKey` がなくなるまで続きを読みます（[Paginating table query results](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Query.Pagination.html)）。ソートキーの形式が崩れた追記記録、INTENT のない操作、必須の項目（種別・主体・開始時刻）が欠けた操作、形式違反の管理情報は、空の結果や欠けた項目のまま返さず、502（`HISTORY_UNAVAILABLE`、保存内容の形式の異常を示すメッセージ）を返します。この変更より前に結果確認中のまま残った作成の操作は、レコード単位の INTENT の写しがないため、確定するとそのレコードの履歴が 502 になります。

閲覧の権限はロールと所有チームで検査します。Curator と Admin はすべてのレコード、Publisher は所属チームが所有チームのレコードだけが読めます。Viewer だけを持つ利用者（Publisher・Curator・Admin のいずれも持たない利用者）は読めません。管理下にないレコード（管理情報がないもの）は404です。取り込み前から公開されていたカタログ項目は、公開承認した主体の操作記録が残っていないため、その主体を「不明」とする公開承認の項目（`state: UNKNOWN`、`principal: null`、`recordVersion` は取り込み時の公開版）を返します。取り込み（#16）は、取り込んだ時点の公開版を管理情報の `adoptedPublishedVersion` に書き込んでください。この値は以後の編集や決裁でも引き継がれるため、取り込み後にポータルで別の版を公開承認しても「不明」の項目は消えません。取り込みが実装されるまでは、この項目が実運用で現れることはありません（テストでは管理情報を直接置いて確認しています）。

結果確認中の決裁（公開承認・却下）は、[ADR-0010](../../docs/adr/0010-settling-uncertain-decisions.md) のとおり、決裁した Curator 本人にだけ中身を返します。それ以外の閲覧者（所有チームの Publisher、決裁していない Curator、Admin）には、種別・主体・開始時刻・結果確認中であることだけを返し、操作 ID・理由・対象のハッシュと版・AWS request ID は返しません（`withheld: true`、`operationId` と `targetHash` は `null`）。

SPA では「操作と審査の履歴を表示」から開きます。Publisher は下書き編集画面、Curator は審査画面、Curator と Admin はカタログ詳細からも開けます（カタログ詳細の未管理レコードは404の案内になります）。作成・編集・申請・公開承認・却下を種別の日本語ラベルで表示し、主体は subject と issuer の組で、決裁の理由も確認できます。0件（「操作の記録はありません。」）と、権限がない（403）・管理下にない（404）・取得できない（502）場合のエラーは区別して表示します。

### IAM

履歴の読み取りには補助テーブルへの `dynamodb:Query` が必要なため、[下書き用モジュールのポリシー](infrastructure/modules/management_drafts/draft-policy.json.tftpl) に追加します。ルートモジュールは既存の [`infrastructure/management`](infrastructure/management/main.tf) をそのまま使います。route（`GET /api/records/{recordId}/history`）は、同じルートモジュールが [route 用モジュール](infrastructure/modules/management_routes/main.tf) で既存 HTTP API の管理 Lambda 統合に割り当てます（「公開停止・廃止・廃止依頼」の「IAM と route」）。

Seam 1 の Stubber と moto で、認可（Viewer と所属チームのない Publisher の拒否、他チームの拒否、Curator と Admin の許可）、補助テーブル未設定時の503、未管理レコードの404、管理下で操作記録のないレコードの0件、作成・申請・公開承認の履歴項目（操作 ID・主体・対象ハッシュ・開始時刻・結果・AWS request ID・決裁理由。作成の対象ハッシュが作成した内容の内容ハッシュであること）、作成・申請・公開承認でアプリが送るすべての追記記録の書き込みに上書き禁止の条件式が付くこと（書き込み要求を記録して検査）と、確定済みの操作の結果を補助ストアから追記し直すと拒否され記録が変わらないこと、作成の INTENT の写しが正本から定義の全文を除いたものと一致すること、結果確認中の作成が履歴に現れること、決裁した主体が不明な項目とその後の公開承認で消えないこと、結果確認中の決裁の中身を決裁した Curator 以外に返さないこと、Query の複数ページの読み取り、形式の崩れた追記記録（ソートキー・主体・種別・開始時刻・INTENT の欠落）の 502 を検証します。Seam 2 の Vitest で、API の応答の検査（旧い値や必須の項目の欠落の拒否）、履歴項目の表示、「不明」と「結果確認中」の表示、0件と403・404・502 の区別、伏せた決裁の表示、開閉ボタンを押すまで API を呼ばないこと、カタログ詳細の導線が Curator・Admin だけに出ることとそこからの404の案内を確認します。実 AWS への書き込みは行っていません。デプロイ後に実環境で、`Query` の IAM 境界と route の追加を確認してください。

## 公開停止・廃止・廃止依頼（#13）

公開停止と廃止は、申請の決裁とは別の、Registry レコードに対する Curator の操作です。どちらも決裁 handler（`handlers/curation.py`）だけで行い、AWS の `UpdateRegistryRecordStatus` で状態を遷移させます。クライアントから AWS の `status` は受け取らず、API ごと（suspend / deprecate）に遷移先を決めます（ADR-0002、設計書「業務上の状態」）。

AWS の状態遷移は [Record lifecycle](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-record-lifecycle.html) に従います。この文書が明記するのは次のとおりです。

- 廃止（`DEPRECATED`）は、どの状態からも遷移でき、元に戻せない終端の状態である（「Any status → DEPRECATED (terminal)」）
- 承認済みのレコードを一時的に発見できなくするには、却下（`REJECTED`）する。再び発見できるようにするには、編集して再承認する
- 承認済みのレコードを編集すると新しい `DRAFT` の版ができ、承認済みの版は再承認まで発見 API に残る（二重リビジョン）

**実環境で未確認の範囲**: 二重リビジョン（承認済みの版を残したまま、最新の版が `DRAFT`・`PENDING_APPROVAL`・`REJECTED`）のときに却下すると、承認済みの版が発見されなくなるかは、文書に記載がなく、実環境でも確認していません。そのため公開停止は、最新の版が `APPROVED` のときだけ受け付けます。それ以外は 409 で拒否し（AWS での作成・更新が失敗した `CREATE_FAILED`・`UPDATE_FAILED` も同じ。理由には失敗した状態であることを示します）、公開管理の画面に理由と手順を表示します。これは [#33](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/33) で実環境の挙動を確かめるまでの暫定の制約で、#13 の受入条件（Curator は理由を付けて公開停止できる）の範囲として Issue #13 で合意しています。#33 の結果に応じて、受付条件を広げるか、この制約を維持します。それまで二重リビジョンのレコードを緊急に止める必要があるときは、審査待ちなら先に申請を却下し、カタログに表示されなくなったかを確かめてください。

| API | 内容 |
| --- | --- |
| `POST /api/records/{recordId}/suspend` | 公開停止。Curator のみ。本文は `{ "reason": 理由 }`（1〜255文字、必須）。最新の版が公開中（`APPROVED`）の管理下のレコードだけが対象。`REJECTED` への却下で発見できなくする。再公開には Publisher による編集と再申請が必要 |
| `POST /api/records/{recordId}/deprecate` | 廃止。Curator のみ。本文は `{ "reason": 理由, "confirm": true }`。理由（1〜255文字、必須）に加えて、確認（`confirm` に `true`）を必須にし、API と SPA の両方で「元に戻せない」ことを明示する。AWS の処理中（`CREATING`・`UPDATING`）と廃止済み以外は、どの状態からも廃止できる |
| `GET /api/records/{recordId}/publication` | 公開管理の照会。Curator のみ。現在の状態、公開中の版（公開停止中と廃止済みは `null`）、公開停止中か（`suspended`）、処理中の操作（`operationId`）、未解決の廃止依頼、操作ごとの可否（`actions.suspend` / `actions.deprecate` の `allowed` と、できない理由の `message`）を返す |
| `POST /api/records/{recordId}/deprecation-requests` | 廃止依頼。管理 handler。対象の所有チームを所属チームに持つ Publisher のみ（所属チームのない Publisher・他チーム・Curator・Viewer は403 `FORBIDDEN`）。所有チームは AWS のレコードを読む前に検査し、所有チームに属さない呼び出し元にはレコードの有無や管理状態を見せない（管理情報がない・管理下にないレコードも、下書きの操作と同じく403）。本文は `{ "reason": 理由 }`（1〜255文字、必須） |
| `POST /api/records/{recordId}/deprecation-requests/decline` | 廃止依頼の見送り。Curator のみ。本文は `{ "requestId": 依頼 ID, "reason": 理由 }`（理由は1〜255文字、必須）。画面で確認した依頼と現在の未解決の依頼が一致するときだけ見送る。レコードの状態は変えない |

### 廃止依頼のしくみ

廃止依頼は AWS の状態を変えません。補助テーブルの `pk` = `REGISTRY#{registryArn}#DEPRECATION`、`sk` = レコード ID の項目に、未解決（`state: OPEN`）の依頼として保存します。依頼の重複は条件付き書き込みで防ぎ、未解決の依頼があるレコードへの再依頼は409 `CONTENT_CONFLICT` です。解決済み（`RESOLVED`）の項目は、新しい依頼で上書きします。レコードごとに未解決の依頼は1件です。

依頼の終わり方は次の2つだけです。

- **廃止**: Curator の廃止の成功と同じトランザクションで、依頼を解決済み（`resolution: DEPRECATED`、`resolutionReason` は廃止理由）にします。
- **見送り**: Curator が理由を付けて依頼を閉じます（`resolution: DECLINED`、`resolutionReason` は見送りの理由）。レコードの状態は変えません。公開停止中ならそのままで、Publisher は編集して再申請できます。

**公開停止は依頼を閉じません。** 廃止は元に戻せないため、運用では「まず公開停止で止め、利用者や参照している Agent への影響を確かめてから廃止する」ことを想定しています。公開停止で依頼を閉じると、廃止するかどうかの判断が誰の手元にも残らないためです。公開停止した依頼は、審査待ちの一覧に「公開停止済み・廃止の判断待ち」として残ります。

依頼の保存と見送りは、どちらもレコードのロックの外では書き込みません。ロックがないことを同じトランザクションの `ConditionCheck` で確かめ、別の操作（公開停止・廃止など）の処理中は409（「別の操作が処理中です」）です。同じトランザクションで操作記録（`AUDIT#…#INTENT` と `AUDIT#…#RESULT`。種別は `REQUEST_DEPRECATION` / `DECLINE_DEPRECATION`、理由を含む）を追記するため、依頼と見送りの理由は履歴（#11）に残り、所有チームの Publisher が確認できます。実行制御（`CONTROL`）とロックは残しません。

`GET /api/reviews`（審査待ちの一覧）は、AWS の承認待ち（`PENDING_APPROVAL`）と未解決の廃止依頼を並べて返します。項目の形は `reviewKind` ごとに異なります。

- `SUBMISSION`（申請）: レコードの要約、申請の補助情報（`submissionState`・`submissionId`・`snapshotHash`・`reason`・`submittedAt`・`submittedBy`・`invalidated`）、`operationId`
- `DEPRECATION_REQUEST`（廃止依頼）: レコードの要約、依頼（`requestId`・`reason`・`requestedAt`・`requestedBy`）、公開停止済みか（`suspended`）、`operationId`

未解決の依頼は補助テーブルが正本で、AWS のページとは数が別に決まります。ページごとに重複して表示しないよう、最初のページ（`nextToken` なし）にだけ、古い順（滞留している順）に並べます。

審査の詳細（`GET /api/reviews/{recordId}`）は、未解決の廃止依頼があれば `deprecationRequest` に添えます。依頼を出した Publisher が再申請した場合に、承認での見落としを防ぐためです。依頼は承認では閉じません。

通知は #15 が担います（「画面上の通知」の節）。公開停止と廃止は AWS のイベント（`Rejected`・`Deprecated`）から所有チームへの通知になりますが、見送りは AWS の状態を変えないためイベントが出ません。廃止依頼と見送りは、アプリ内で発生する通知として、依頼・見送りと同じトランザクションで書きます。

### 決裁と同じ手順

公開停止・廃止も決裁（#10）と同じ手順で行います。レコード単位のロックと意図の記録（`AUDIT#…#INTENT`、種別は `SUSPEND` / `DEPRECATE`）→ 現物の照合 → `UpdateRegistryRecordStatus`（`statusReason` は理由）→ 再取得しての確定です。申請を対象としないため、スナップショットのハッシュは受け取らず、代わりに開始時の現物の内容ハッシュ（`contentHash`）を固定します。

照合の時点の状態（`observedStatus`）も操作に記録します。再取得してもその状態が見えている間は、操作が未反映と判断します。

- AWS の受け付けを確認できていない操作は、同じ `Idempotency-Key` の再送（決裁 handler）で送り直します。
- 受け付けを確認した後も反映が見えないときは、失敗にせず結果確認中（202）のままロックを持ちます（ADR-0010）。確定できなければ Admin の回復操作（#12、ADR-0011）で確定します。

操作照会（`GET /api/operations/{operationId}`）は操作した Curator 本人だけが参照できます。履歴（#11）には「公開停止」「廃止」として表示されます。

成功すると、管理情報の `activeSubmissionId` を消します。公開版スナップショット（`PUBLISHED#版`）と管理情報の `publishedVersion` は書き換えません。

- **公開停止**: 管理情報に公開停止の印（`suspendedVersion` = 公開停止した時点の公開中の版）を書きます。印がある間は、API が返す公開中の版（`publishedVersion`）は `null` になり、「新しい版の却下で旧版は公開中」の場合と区別できます。`publishedVersion` は再申請の semver の下限として引き続き使います。印は公開承認の成功で消えます。印が残っていても、AWS が公開中（`APPROVED`）を返していれば公開停止中とは扱いません（ポータルの外で再承認された場合。[ADR-0012](../../docs/adr/0012-suspension-mark-and-deprecation-requests-in-supplement.md)）。
- **廃止**: 公開中の版は `null` になります。公開版スナップショットは残るため、公開の記録は失われません。

### SPA

- **Curator（公開管理）**: 公開停止・廃止・見送りは、公開管理のパネルにまとめています。パネルは次の3つの入口から開けます。
  - カタログの詳細: 公開中のレコードを名前で探して、すぐに止めるための主な経路
  - 審査待ちの廃止依頼の「対応する」
  - 「公開管理」の節でのレコード ID の指定: 公開停止中など、カタログに出ないレコードを扱う
- **パネルの表示と操作**:
  - 現在の状態、公開中の版、未解決の廃止依頼（経過日数）、処理中の操作、履歴を表示します。実行できない操作は、サーバーが返した理由を表示して送らせません。
  - 理由の入力欄は、公開停止・廃止・見送りでそれぞれ分けています。
  - 廃止は二重確認が必須です。1段目で理由を入力し「廃止は元に戻せないことを確認しました」をチェックして「廃止する」を押すと、送信せずに最終確認（`alertdialog`）を開きます。2段目でレコード名を入力し、一致したときだけ「廃止を確定する」で送信します。「やめる」で最終確認を閉じると、入力したレコード名は消えます。API は `confirm: true` のない廃止を拒否します（二重確認を終えていない呼び出しの誤送信を防ぐ最後の検査）。
  - `Idempotency-Key` は操作ごとに持ち、結果が不明な失敗（5xx・通信エラー）のときだけ同じキーで再試行します。結果確認中（202）は決裁と同じ操作照会で確定します。
- **Curator（審査待ち）**: 未解決の廃止依頼は、一覧の最初のページに古い順で表示します。依頼者・理由・経過日数・状況（未対応、または公開停止済み・廃止の判断待ち）も表示します。審査の詳細は、未解決の依頼があれば警告します。
- **Publisher（下書きの編集画面）**: 廃止済みでない自分のチームのレコードに「廃止を依頼する」のフォーム（理由は必須）があります。409 では、サーバーの理由（未解決の依頼の重複、または別の操作の処理中）を表示します。結果（廃止・見送りの理由）は操作履歴で確認できます。

### IAM と route

公開停止・廃止・公開管理の照会・見送りの route は、[決裁用 Terraform モジュール](infrastructure/modules/decision/main.tf) に追加しました。

- route: `POST /api/records/{recordId}/suspend`、`POST /api/records/{recordId}/deprecate`、`GET /api/records/{recordId}/publication`、`POST /api/records/{recordId}/deprecation-requests/decline`
- Lambda の呼び出し許可: path ごとに `local.curator_record_paths` で管理します。route も同じ定義（メソッドと path）から作るので、操作を足すときはここに1件加えます。バックエンドでは、パラメーターを含む route をルート表のキー（`app.py`）そのものと照合するため、別の一覧は持ちません。

決裁 Lambda の実行ロールは、状態遷移（`UpdateRegistryRecordStatus`）に必要な権限をすでに持ちます。補助テーブルについては、決裁ロールに次の2つを追加しています。

- `dynamodb:Query`: 審査待ちの一覧が未解決の廃止依頼を読むため
- `dynamodb:ConditionCheckItem`: 見送りがロックを確かめるため

廃止依頼の route（`POST /api/records/{recordId}/deprecation-requests`）は、履歴の route（`GET /api/records/{recordId}/history`）とあわせて、[管理 handler の route 用モジュール](infrastructure/modules/management_routes/main.tf) で既存 HTTP API の管理 Lambda 統合に割り当てます。ルートモジュール [`infrastructure/management`](infrastructure/management/main.tf) から呼び、次の値を `terraform.tfvars` に追加してください。

- `http_api_id`・`jwt_authorizer_id`: 決裁モジュールと同じ既存 HTTP API と JWT authorizer
- `management_integration_id`: カタログや下書きの route が使っている、管理 Lambda への既存の統合（integration）の ID
- `management_function_name`: 既存の管理 Lambda の関数名（この2つの path に限った呼び出し許可を追加する）

HTTP API・authorizer・統合は作らず借用します。#5 の IaC が HTTP API と管理 Lambda の統合を定義したら、このモジュールをその IaC から呼び出し、このルートモジュールの state から取り除いてください。管理 Lambda のロールには、廃止依頼の保存でロックを確かめるための `dynamodb:ConditionCheckItem` を [下書き用モジュールのポリシー](infrastructure/modules/management_drafts/draft-policy.json.tftpl) に追加しています。

Seam 1 の Stubber と moto で、次を検証します。

- 廃止依頼: 認可（所有チームの検査、所属チームのない Publisher・他チーム・Curator・Viewer の403）、本文の検査（理由の必須化と255文字上限、秘密情報の拒否、未知の項目）、未解決の依頼の重複・ロック中・廃止済みの409、未管理レコードと管理情報のないレコードの403（AWS を読む前）、所有チームに対する AWS から消えたレコードの404、補助テーブル未設定の503、履歴への記録
- 審査待ちの一覧: 種別ごとの項目の形、最初のページだけの表示と古い順、2ページ目に重ねないこと、保存形式の崩れと補助ストアの障害の502、審査の詳細での依頼の警告
- 公開停止: 依頼を閉じないこと、公開中の版が `null` になり semver の下限が残ること、公開版スナップショットの不変、公開版のない審査待ちと二重リビジョンの409、直接承認の拒否、編集→再申請→承認での再公開と印の消去、反映待ちの結果確認中と操作照会での確定、受け付け前に止まった操作の同じキーでの再送
- 廃止: 確認の必須化、依頼の解決と理由の記録、審査待ちからの廃止、廃止後の公開停止・廃止・依頼の409
- 見送り: 解決と履歴への記録、状態を変えないこと、見送り後の再依頼、認可・本文・依頼 ID の不一致・ロック中の拒否
- 公開管理の照会: 状態・依頼・操作の可否、二重リビジョンとロック中と未管理の理由、認可、404 と 502 の区別
- Publisher・Viewer・Admin の403、`Idempotency-Key` の必須化

IaC は `test_draft_permissions.py` で、route 用モジュールが履歴と廃止依頼の route だけを既存の管理 Lambda の統合に割り当てること、呼び出し許可がその path に限られること、ルートモジュールから適用されることを検査します。

Seam 2 の Vitest で、次を確認します。

- 審査待ちの一覧の応答の検査（種別ごとの必須の項目、申請の形の廃止依頼の拒否）、経過日数と判断待ちの表示、「対応する」からの公開管理、審査の詳細の警告
- 公開管理の公開停止・廃止・見送りの本文、理由の欄が分かれていること、二重確認（確認のチェックとレコード名の入力を経るまで送らないこと、最終確認をやめたときに送らないこと）、実行できない理由の表示、409 の再読み込み、不明な失敗の同じキーでの再試行、結果確認中の確定、応答の検査、レコード ID の入口とカタログの入口が Curator だけに出ること
- Publisher の依頼フォームの本文、409 の理由の表示、応答の検査

実 AWS への書き込みは行っていません。デプロイ後に実環境で、route の追加と IAM 境界、二重リビジョンでの却下の挙動を確認してください。

## 回復操作（#12）

結果確認中のまま止まった操作（決裁した Curator や下書きの Publisher が不在、補助ストア障害が続くなど）を、Admin が Registry の再取得で確定する回復操作です。判断は [ADR-0011](../../docs/adr/0011-admin-recovery-operation.md) に記録しました。ロックは期限で解除せず、確定するまで別の書き込みを409で止めます。確定できない操作のロックを外すのは、Admin が Registry の状態を確かめたうえで行う強制解除だけです。

- `GET /api/records/{recordId}/recovery`（Admin）: レコードのロックを持つ操作の概要（種別・主体・開始時刻・回復できるようになる時刻 `recoverableAt`・`recoverable`。操作 ID は返しません）を返します。結果確認中の操作がなければ `operation: null` です。理由やハッシュなど、操作の中身は返しません。
- `POST /api/records/{recordId}/recovery`（Admin、本文は `reason`（1〜255文字）と任意の `release`）: Registry を再取得して操作を確定します。下書きの操作（作成・更新・申請）も決裁の操作も、観測できる状態だけから確定し、申請 API・状態更新 API は呼び直しません（決裁 Lambda は申請の権限を持たず、決裁の再送は決裁した Curator に限るため）。確定できれば200で `SUCCEEDED` または `FAILED`、確定できなければ202の `VERIFYING` です。`release: true` を指定し、Registry を再取得できても確定できないときだけ、操作を失敗（`ADMIN_RELEASED`）として確定し、ロックを解除します。Registry を読めなければ解除せず502 `REGISTRY_UNAVAILABLE`、AWS が処理中（`CREATING`・`UPDATING`）なら解除せず409 `RECORD_STILL_PROCESSING` です。AWS が受け付け（成功応答）を返したと記録済みの決裁・申請は、反映が見えなくても AWS に届いているため解除せず409 `ACCEPTED_BY_REGISTRY` です（反映を待って照合します）。補助テーブルを読み書きできない、またはロックが指す操作がないときは502 `SUPPLEMENT_UNAVAILABLE` です。
- 開始から5分が過ぎていない操作は、実行中の可能性があるため回復できません（409 `OPERATION_STILL_RUNNING`）。ロックを持つ操作がなければ409 `NO_RECOVERABLE_OPERATION` です。
- 回復した主体・時刻・理由・方法（`RECONCILED` は Registry の再取得で確定、`RELEASED` は強制解除）は、確定した操作と操作記録（RESULT）に残ります。`GET /api/operations/{operationId}` と履歴（`GET /api/records/{recordId}/history`）の項目にも `recovery` として載り、強制解除された操作の失敗の message には強制解除と理由が含まれます。
- 回復の応答に操作 ID と操作の中身は含めません（結果確認中の決裁の操作 ID は決裁した Curator 以外に返さない、ADR-0010）。
- `clientToken` を持つ Registry 管理 API は作成（[`CreateRegistryRecord`](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/create_registry_record.html)）だけで、[更新](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/update_registry_record.html)・[申請](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/submit_registry_record_for_approval.html)・[状態更新](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/update_registry_record_status.html)にはありません（公式ドキュメントと、botocore のサービスモデル `agent-registry-control/2025-12-01` で確認）。作成は同じ操作 ID で再試行し、更新・申請・状態更新は照合で確定します。
- Lambda を再起動しても、結果確認中の操作は補助テーブル（`LOCK` と `OP#…`）から取得できます。DynamoDB の項目を手作業で消して再実行しないでください。
- レコード ID が決まる前に失敗した作成は、レコードのロックを持たないため回復の対象外です。同じ入力と `Idempotency-Key` で再試行します。
- 強制解除した操作が実際には AWS に反映されていた場合、Registry の状態とポータルの記録（公開版スナップショットなど）が食い違います。強制解除の前に Registry の状態を確認してください。

SPA の「回復操作」は Admin（`registry-admin` グループ）だけに表示します。Registry レコード ID から結果確認中の操作を確認し（回復できる時刻はバックエンドの `recoverableAt` を表示します）、理由を付けて「Registry を再取得して照合」を実行します。確定できず結果確認中のままの場合だけ、確認のチェックを入れてから「強制解除する」を実行できます。結果確認中・成功・失敗と回復結果（方法・主体・時刻・理由）は、取得や回復の失敗（403・409・502）とは別の表示です。Publisher と Curator の「処理結果を確認」「決裁の結果を確認」でも、回復操作で確定した操作は回復結果を表示してから結果に進み、操作と審査の履歴にも回復結果を表示します。

Seam 1 の Stubber と moto で、Admin だけが実行できること（Curator・Publisher・グループなしは403）、本文の検査（`reason` の必須化と255文字上限、未知の項目の拒否）、拒否した要求が AWS もロックも変えないこと、開始直後の操作と、ロックを持つ操作がないレコードの409、結果確認中の決裁・更新を Registry の状態から確定して回復の主体と理由を操作記録に残すこと、申請 API を呼び直さないこと、確定できない操作の強制解除（Registry を再取得できないとき、AWS が受け付けを返した決裁・申請は解除しない）、操作照会と履歴への回復結果の反映（決裁した Curator と更新した Publisher）、補助ストアの異常を Registry の障害と区別する502、回復の応答に操作の中身を含めないこと、決裁と申請について AWS の403・429・5xx・503・タイムアウトで成功を記録せず、結果不明のときだけロックを残すこと、Lambda の再起動後も操作を取得して回復できることを検証します。AWS 成功後の補助ストア障害は、既存の作成・申請・決裁のテストで検証しています。Seam 2 の Vitest で、Admin 以外への非表示と API を呼ばないこと、結果確認中の操作の表示、操作なし・取得失敗・409の区別、回復できない操作の案内（`recoverableAt` の表示と、それを欠いた応答の拒否）、AWS が受け付けた操作の強制解除の拒否の表示、Publisher・Curator の照会と履歴での回復結果の表示、照合と強制解除の本文（強制解除は確認の後だけ）、確定した結果と回復結果の表示を確認します。実 AWS への書き込みは行っていません。デプロイ後に実環境で、決裁 Lambda の route の追加と、Admin による回復を確認してください。

## 既存レコードの取り込み・所有変更・接続診断（#16）

Admin の「Registry 管理」から、未管理レコードを所有チームへ割り当てて取り込み、管理済みレコードの所有チームを変更できます。Admin 以外には画面も接続情報も表示せず、API は AWS・補助テーブルへ問い合わせる前に403を返します。取り込み前の Publisher による編集・申請は引き続き403です。所有チームは `team-` で始まる既存の Cognito グループ名を入力します。この画面はグループを作成せず、存在の確認もしません。

| API | handler | 入力・応答 |
| --- | --- | --- |
| `GET /api/admin/records?management=unmanaged` | 管理 | 未管理の `items` と `nextToken`。`management=managed` で所有変更の対象を取得。省略時は未管理 |
| `GET /api/admin/connection` | 管理 | 固定設定の `registryArn`・`region`、観測した `authorization`、`connection`、`autoApproval`、`warnings` |
| `POST /api/records/{recordId}/adopt` | 決裁 | `ownerTeam`、`reason`。`Idempotency-Key` 必須 |
| `POST /api/records/{recordId}/reassign-owner` | 決裁 | `ownerTeam`、`reason`、一覧で取得した `expectedManagementHash`。`Idempotency-Key` 必須 |

`connection` は通信に成功すれば `{"state": "CONNECTED"}`、GetRegistry に失敗すれば `{"state": "ERROR", "code": "REGISTRY_CONNECTION_ERROR", "message", "cause"}` です。`cause` は接続の問題を切り分けるための原因で、AWS のエラーコード（`AccessDeniedException`・`ThrottlingException` など）か、AWS の応答がない失敗の例外名（`EndpointConnectionError` など）です。画面は案内文と原因を並べて表示します。

一覧は Registry 管理 API の1ページ（最大50件）と管理情報を照合します。ページ内の全件が管理済みなら未管理の `items` は空でも `nextToken` が残ります。トークンがなくなるまで次ページを確認してください。通信障害・管理情報の破損を0件へ置き換えません。各項目はレコードID・名前・表示名・種別・版・AWS状態・`managed`・`ownerTeam`・`managementHash` を持ちます。

成功応答は `recordId`・`ownerTeam`・`operationId`・`operationState: SUCCEEDED`・`publishedVersion`（公開版がなければ null）・`a2aBackfillRequired` です。後者は取り込み時の公開 Agent に descriptor と一致する真偽値の `isA2A` がなければ true です。取り込みは Registry を更新しないため、補完は所有チームの保存・申請・公開承認で行います（ADR-0006）。補完が必要なことは取り込んだ Admin への応答だけでなく、所有チームが開く取り込みレコードの編集画面（`initializationNotice`）にも、保存で補完されるまで表示します。

### 取り込みと監査の保存

取り込みは Registry の descriptor・metadata・公開状態を更新しません。現在の下書きは管理 API、現在公開中の版は JWT 発見 API の batch-get から別々に読みます。新しい下書きと古い公開版が共存していても、公開版の連絡先等に下書きの値を混ぜません。発見 API の確定的な不存在だけを公開版なしとして扱います。アクセス拒否・通信障害・不正応答では502、管理 API が返す公開版の発見反映待ちでは409を返し、取り込みません。

対応する primary descriptor が1個ある Agent・MCP サーバー・Skill を取り込めます。descriptor から読める補助情報を管理情報へ写します。A2A・MCP・Skill の連絡先等、元の定義から取得できない補助情報は空文字で保存し、所有チームが通常編集で補います。取り込んだレコードに environment が未設定なら、設計書の既定値 dev を初期値として表示し、その旨を案内します。dataClassification には仕様上の既定値がないため推測せず、未選択（空文字。バックエンドと SPA の `UNSET_DATA_CLASSIFICATION`）のまま表示して public / private の選択を案内します。未選択のままの保存は400です。読み取りだけでは Registry を更新せず、所有チームが確認して保存します。confidential 等の明示的な不正値を初期値で上書きしません。

取り込んだレコードの更新では、ポータルが管理しない既存の custom metadata の項目を保持し、ポータルの項目（所有チーム・環境・データ分類・`isA2A`）だけを上書きします。ポータルで作成したレコードの更新は、従来どおりポータルの項目だけで metadata を置き換えます。

表示名・説明が未設定なら、表示名は既存の `name`、説明は空欄を初期値として表示し、補完を案内します。`v1` 等の旧形式の版は元の文字列のまま表示します。所有チームは説明を補い、semver の版を指定して保存してから申請してください。保存・申請時の必須項目と semver の検証は変わりません。取り込み時に記録した公開版（`adoptedPublishedVersion`）が semver でない場合は、ポータル導入前の版として、semver のどの版よりも前とみなします。所有チームは任意の semver の版（例 `1.0.0`）で申請できます。取り込み後に semver の版が公開されたら、以降はその版と比較します。取り込み時の版と異なる semver でない版が公開版として観測された場合は、順序を決められないため申請を400で拒否します。ポータルは公開版の値を自動置換せず、取り込み時の公開版と公開版スナップショットを保持します。

`MANAGEMENT`、公開版がある場合の `PUBLISHED#<recordVersion>`、追記専用の `AUDIT#<操作ID>#INTENT/#RESULT`、再送用の結果を一つの DynamoDB transaction で保存します。すべての書き込みと共通の `LOCK` がないこと、および読取時の管理情報との一致を同時に検査します。既存の公開スナップショットは上書きしません。衝突時は409で再読み込みを案内します。AWS が `CREATING` / `UPDATING` の間も409です。

公開版があれば `publishedVersion` と `adoptedPublishedVersion` に、その公開版の文字列を保存します。取り込み前の承認者・承認日時や最終編集者に Admin を代入しません。履歴の先頭には `APPROVE`・`UNKNOWN`・`principal: null` の項目が現れ、後の編集・申請・所有変更・公開承認後も残ります。取り込み・所有変更自身の監査種別は `ADOPT` / `REASSIGN_OWNER` です。主体・対象ハッシュ・理由・時刻、所有変更では旧チームと新チームを記録します。

所有変更は管理情報全体を保持して `ownerTeam` だけを変更し、公開スナップショットを変更しません。旧チームの Publisher は直ちに編集できなくなります。審査中の内容ハッシュは一致しなくなるため、古い申請の決裁は409です。新チームが下書きを保存し、申請し直してください。Registry の検索用 `ownerTeam` の写しは次回の通常編集・公開まで古い場合があります。

瞬時の補助ストア操作なので `CONTROL` と永続ロックは作りません。transaction の応答が失われても、保存した結果を強整合で読み直して成功を確認します。結果も読み取れなければ503を返します。SPA は同じ内容の再試行で同じキーを使います。再送結果は7日間保持し、監査項目には TTL を付けません。結果が不明な間は同じ内容・同じキーで再試行してください。ポータル外の変更との排他は ADR-0001 の書き込み経路集約を前提とします。

### 接続情報と設定逸脱

ローカル Vite と実際の管理画面・API クライアントを Chromium で操作し、取り込み、所有チーム変更、自動承認警告、接続失敗表示を確認しました。API 応答には fixture を使用しています。実 AWS・Cognito との結合試験ではありません。確認画面は[所有チーム変更](docs/screenshots/issue16-ownership.png)、[自動承認警告](docs/screenshots/issue16-drift.png)、[接続取得失敗](docs/screenshots/issue16-error.png)を参照してください。

`GetRegistry` の `discoveryConfiguration.authorizerType` を `CUSTOM_JWT` / `AWS_IAM` として表示し、`approvalConfiguration.autoApprovalRules` が省略または空なら自動承認無効、非空なら警告を表示します。[自動承認設定](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_ApprovalConfiguration.html)は「省略または空なら手動審査」と定めています。設定や規則の型が契約と異なる応答だけを `UNKNOWN` とし、無効であるとは推測しません。botocore のモデルでは `discoveryConfiguration.authorizerType` も任意です。`GetRegistry` が成功して認可方式だけがない場合は、接続済みのまま認可方式を未取得（`null`）として警告します。接続エラーとするのは `GetRegistry` の呼び出し自体が失敗したときだけです。その場合も設定由来の ARN・リージョンを保持し、認可方式未取得と日本語の接続エラーを表示します。この診断はレコードの試験呼び出しを行いません。

申請 API も同じ判定（`domain/submissions.py` の `auto_approval_state`）を使い、自動承認が無効（`DISABLED`）のときだけ申請します。有効（`ENABLED`）と未取得（`UNKNOWN`）では、既存の動作どおり503 `APPROVAL_CONFIGURATION_CHANGED` で申請を止めます。そのため、画面の表示と申請の可否は常に一致します。Seam 1 では、同じ設定の組（省略・空・非空・型違い）を接続情報 API と申請 API の両方に与え、表示と申請の可否が一致することを確認します（`tests/draft_helpers.py` の `AUTO_APPROVAL_CASES`）。

根拠は [GetRegistry](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_GetRegistry.html)、[ListRegistryRecords](https://docs.aws.amazon.com/cli/latest/reference/agent-registry-control/list-registry-records.html)、[BatchGetDiscoverableRegistryRecord](https://docs.aws.amazon.com/agent-registry/latest/APIReference/API_BatchGetDiscoverableRegistryRecord.html) です。GetRegistryRecord には公開承認済みの版を指定する引数がないため、下書きを公開版として保存しません。

### A2A metadata の補完と有効化

1. Admin が既存レコードを取り込み、所有チームに引き渡します。
2. 所有チームの Publisher が下書きを読み、公開中の版より大きい semver の版を通常編集で保存します。保存時に descriptor から `isA2A` を導出し、既存の任意 metadata を保持したまま、所有チーム・環境・データ分類をフォームの値で更新します。未取得の連絡先等もこの時に補います。
3. 通常の申請と、申請者・最終編集者以外の Curator による公開承認を行います。ポータル外の審査待ちレコードも、そのまま承認せず、保存・再申請から進めます。
4. 検索への反映を待ち、公開中の AGENT を全ページ取得し、`isA2A` が真偽値で存在し descriptor と一致することを実 AWS で確認します。
5. A2A の一覧を全ページ取得し、`a2a=true` と `a2a=false` の検索結果と照合します。検索は上位20件なので必要に応じてクエリを分け、一覧を全件の正本として漏れを確認します。
6. 照合が完了した後に #5 の IaC で管理 Lambda の `A2A_SEARCH_ENABLED=true` を設定します。取り込み・編集・承認はこのフラグを自動変更しません。

この変更の自動検査は Stubber・moto・fetch fake までです。実 AWS の既存レコード補完、検索インデックスの全ページ照合と A2A 検索有効化は実施していません。

### 決裁 Lambda の追加設定と権限

`infrastructure/decision/terraform.tfvars.example` に、管理 Lambda と同じ発見用 M2M の token URL、client ID、client secret ARN、scope を設定します。決裁 Lambda に追加する AWS 権限は、その secret に対する `secretsmanager:GetSecretValue` だけです。JWT 発見 API は M2M で認可します。descriptor 更新・schema 更新・Registry 更新権限は追加しません。管理一覧・接続診断は管理 Lambda、取り込み・所有変更は決裁 Lambda への専用 route と呼び出し許可で公開します。借用 Registry を Terraform resource にしません。

## 画面上の通知（#15）

Registry の状態変化と廃止依頼・見送りを、画面の通知欄に届けます。メール・SNS・WebSocket は使いません（設計書「通知」）。

| 出来事 | 発生元 | 宛先グループ | 種別（`kind`） |
| --- | --- | --- | --- |
| 申請（`Pending Approval`） | Registry のイベント | Curator 全員 | `SUBMITTED` |
| 廃止依頼 | アプリ内（依頼と同じトランザクション） | Curator 全員 | `DEPRECATION_REQUESTED` |
| 公開承認・却下・公開停止・廃止（`Approved`・`Rejected`・`Deprecated`） | Registry のイベント | 所有チームの Publisher 全員 | `APPROVED`・`REJECTED`・`SUSPENDED`・`DEPRECATED` |
| 廃止依頼の見送り | アプリ内（見送りと同じトランザクション） | 所有チームの Publisher 全員 | `DEPRECATION_DECLINED` |
| 参照先の Skill・MCP サーバーの公開停止・廃止 | Registry のイベント | 参照先を公開中の版で使う Agent の所有チーム（チームごとに1件） | `REFERENCE_SUSPENDED`・`REFERENCE_DEPRECATED` |
| 操作記録と照合できない状態変化（ポータル外の変更） | Registry のイベント | Admin 全員。管理下のレコードなら所有チームの Publisher 全員にも | `OUTSIDE_CHANGE` |

宛先グループは、Curator（`registry-curator`）・Admin（`registry-admin`）・所有チームの Publisher（`registry-publisher` と `team-*`）の3種類です。通知はグループ単位で1件だけ作り、既読はユーザー（主体）単位の既読位置で管理します。

### イベント処理

EventBridge の default バスに Registry が送るイベント（source `aws.agent-registry`、detail-type `Registry Record State changed to Draft`・`Pending Approval`・`Approved`・`Rejected`・`Deprecated`）を、ルール1本でイベント処理 Lambda（`handlers/events.py`）に流します。イベントの `detail` には `registryRecordId` と `registryId` しかなく、操作者は含まれません（[Registry EventBridge イベント](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-eventbridge.html)、[イベントの形式](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-notifications-approvals.html)）。ルールは借用する Registry の ID（`detail.registryId`）に絞り、handler も source・detail-type・Registry ID・record ID の形式を確かめ、対象外のイベントは Registry を読まずに無視します。

1. **重複除去**: 処理を終えた event ID は補助テーブルに印を残し、同じ event ID の再配信は Registry を読まずに終えます。印は通知をすべて書いた後に書くため、途中で失敗したイベントは再配信で処理し直せます。
2. **再取得**: [`GetRegistryRecord`](https://docs.aws.amazon.com/agent-registry-control/latest/APIReference/API_GetRegistryRecord.html) でレコードを読み直し、イベントの detail-type ではなく、再取得した状態（`status`）と更新時刻（`updatedAt`）を使います。順序が入れ替わって申請のイベントが承認の後に届いても、承認として扱います。`status`（値の一覧は固定版 botocore のモデル `agent-registry-control/2025-12-01` の `RegistryRecordStatus`）と `updatedAt` は応答の必須項目です。処理中・失敗の状態（`CREATING`・`UPDATING`・`CREATE_FAILED`・`UPDATE_FAILED`）は遷移が確定していないため通知せず、確定した後の遷移のイベントで通知します。レコードが削除されていれば（`ResourceNotFoundException`）通知せずに処理済みにします。
3. **照合**: レコードの操作記録（`AUDIT#…`）のうち、AWS の状態を変える操作（作成・編集・申請・公開承認・却下・公開停止・廃止）で、状態の変化より前に始まった最も新しいものが、次をすべて満たすときだけ、ポータル内の変更とみなします。満たさなければ「ポータル外の変更」として Admin に届けます（管理下のレコードなら所有チームにも。下の「宛先」）。
   - 観測した状態に遷移させる種別である（`DRAFT` は作成・編集、`PENDING_APPROVAL` は申請、`APPROVED` は公開承認、`REJECTED` は却下・公開停止、`DEPRECATED` は廃止）
   - 失敗していない（強制解除した操作を含む）
   - 確定している場合は、AWS の `updatedAt` が操作の開始から確定（`settledAt`）までの間にある。結果確認中（RESULT がない）の操作は、確定を待たずにポータル内とみなす

   過去に同じ種別の操作があっても、その後に別の操作や時間をおいた変化が挟まれば説明できません（例: ポータルで承認した1時間後の再承認はポータル外）。Lambda と AWS の時計のずれとして前後5分を許容します。確定時刻は、この変更から操作記録の RESULT に記録します。それより前に確定した操作は、開始から15分（Lambda の上限）までの変化と照合します。未管理レコード（取り込み前）の変化は、ポータルの操作記録がないため常にポータル外の変更です。ポータル内の作成・編集による `DRAFT` は通知しません。
4. **宛先**: 所有チームは管理情報の `ownerTeam`（正本）から決めます。公開停止と却下は、照合した操作の種別（`SUSPEND` / `REJECT`）で区別します。廃止はポータル外の変更でも元に戻せないため、Admin に加えて参照先を使う Agent の所有チームにも届けます。
5. **書き込み**: 同じ状態変化（レコード・状態・`updatedAt`）から作る通知は、宛先グループと種別ごとに重複除去の印と通知を1つのトランザクションで書きます。別の event ID で届いた同じ状態変化からは、通知を作り直しません。

Registry・補助テーブルの一時的な失敗（スロットリング・5xx など）では、handler は例外を返して Lambda の非同期呼び出しの再試行（最大2回、イベントの経過時間1時間まで）に任せます（[非同期呼び出しのエラー処理](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-error-handling.html)）。再試行しても通知は増えません。再試行を使い切ったイベントは、元のイベントの本文のまま DLQ（SQS、`<name_prefix>-dlq`、14日保持）に残ります。原因を取り除いた後、Admin がメッセージの本文をイベント処理 Lambda に非同期で送り直してください（`aws lambda invoke --invocation-type Event --payload file://event.json --cli-binary-format raw-in-base64-out <関数名> /dev/null`）。処理済みのイベントと同じ状態変化からは通知を作り直さないため、送り直しで重複は起きません。

#### 照合の限界

- **結果確認中の操作**: 結果確認中（RESULT がない）の操作は、決裁の再送や申請の再開で AWS への反映が開始から遅れることがあるため、時間で区切らずにポータル内とみなします。そのため、操作が結果確認中のままロックを持っている間に、同じ種別の遷移がポータル外で起きても検出できません。回復操作（#12）で確定した操作も、確定時刻は回復を実行した時刻になるため、開始から回復までの変化をポータル内とみなします。ロックがある間はポータルの書き込みが止まり、回復操作で Admin が Registry を確認するため、この範囲は回復の手順で補います。Registry への送信時刻を記録して照合を区切る改善は [#38](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/38) で行います。
- **宛先**: ポータル外の変更は Admin 全員に届けます。管理下のレコードなら、所有チームの Publisher 全員にも同じ種別（`OUTSIDE_CHANGE`）で届け、自分のレコードがポータルの外で公開・却下などされたことに気づけるようにします。本文は宛先で分け、Admin には変更経路の確認を、所有チームには経緯を Admin に確かめることを促します。所有チームに通常の公開承認・却下などの種別で届けたり、Curator に申請として届けたりはしません。確認されていない変化を通常の申請・決裁と同じに見せないためで、ポータル外の審査待ちはそのまま決裁せず、取り込み・保存・再申請から進めます（「A2A metadata の補完と有効化」）。未管理レコードにはポータル上の所有チームがないため、Admin にだけ届けます。元に戻せない廃止は、参照先を使う Agent の所有チームにも届けます。
- **途中の遷移**: 順序が入れ替わったり処理が遅れたりして、再取得した時点で次の遷移が済んでいれば、途中の遷移（例: 承認済みの申請）は通知しません。

### 参照先の逆引き

参照先から Agent を引くため、公開承認の確定と同じトランザクションで、公開した版の関連参照を逆引きの項目（`pk` = `REGISTRY#{registryArn}#REFERENCED#{参照先の record ID}`、`sk` = Agent の record ID）に書きます。新しい版で参照をやめても項目は消さず、通知のときに Agent の管理情報の `publishedVersion` の公開版スナップショット（`relatedReferences`）を読み直して、今も参照している Agent だけに届けます。関連参照が生まれるのはポータルでの公開承認だけです（取り込み（#16）は公開版スナップショットの関連参照を常に空で保存します）。そのため、逆引きが欠けうるのは、この変更より前に関連参照つきで公開承認された Agent だけです。MVP1 は実環境への反映前のため、既存データの移行スクリプトは作りません。

**デプロイ前の確認**: #15 を反映する環境に、#15 より前に関連参照つきで公開承認した Agent があれば、その Agent を再申請・公開承認して逆引きを作ってください。補助テーブルで `PUBLISHED#…` の項目のうち `relatedReferences` が空でないものを確かめ、対応する `REGISTRY#{registryArn}#REFERENCED#{参照先の record ID}` の項目があるかで判断できます。なければ、次の公開承認までその Agent の所有チームに参照先の公開停止・廃止の通知が届きません。

### 保存形式と保持期間

補助テーブルの次の項目は、すべて TTL 属性 `expiresAt` に90日後の時刻を持ちます（設計書「補助テーブルに保存するもの」）。DynamoDB の TTL による削除は期限から遅れることがあるため（[TTL の仕組み](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/howitworks-ttl.html)）、期限切れの項目は一覧・未読件数・既読位置の読み取りで除きます。

| 項目 | `pk` | `sk` |
| --- | --- | --- |
| 通知 | `REGISTRY#{registryArn}#NOTIFICATION#{宛先グループ}`（`CURATOR`・`ADMIN`・`TEAM#{team}`） | `{時刻}#{通知 ID}`（既読位置。時刻は UTC・マイクロ秒までの固定桁） |
| 重複除去の印 | `REGISTRY#{registryArn}#NOTIFIED#{出来事のキー}` | `{宛先グループ}#{種別}` |
| 処理済みのイベント | `REGISTRY#{registryArn}#EVENT#{event ID}` | `EVENT` |
| 既読位置 | `REGISTRY#{registryArn}#READ#{issuer}#{subject}` | `READ` |

通知は宛先グループ・種別・レコード ID・表示名（アプリ内の見送りでは Registry を読まないため持たない）・本文・時刻・EventBridge の event ID（イベント由来のもの）を持ちます。アプリ内の通知は、元の操作（廃止依頼・見送り）と同じトランザクションで書くため、依頼が409で拒否されれば通知も残りません。

### API

- `GET /api/notifications`（管理 handler）: 呼び出し元の宛先グループの通知を新しい順に最大50件、未読件数（既読位置より後の通知の数。50件を超えても全件を数える）、既読位置（`readUpTo`、未設定なら `null`）を返します。各通知は `notificationId`・`position`・`kind`・`recordId`・`recordName`（`null` あり）・`message`・`createdAt`・`read` です。宛先グループを持たない Viewer と所属チームのない Publisher は0件です。補助テーブルが未設定なら503 `NOTIFICATIONS_NOT_CONFIGURED`、読めないか保存内容の形式が崩れていれば502 `NOTIFICATIONS_UNAVAILABLE` で、0件とは区別します。
- `POST /api/notifications/read`（管理 handler）: 本文 `{ "readUpTo": 通知の position }` で、その位置までの通知を既読にします。応答は一覧と同じ形です。既読位置は前にだけ進み、古い位置を送っても戻りません（複数のタブからの更新に備える）。形式の誤り・未知の項目は400 `VALIDATION_ERROR` です。

### SPA

ヘッダーの通知欄に未読件数（「通知（未読 N 件）」）を表示し、表示している間は約60秒ごとにポーリングします。押すと一覧（種別・レコード・本文・時刻・未読の印）を開き、「すべて既読にする」で最新の通知の位置まで既読にします。0件（「通知はありません。」）と取得の失敗（再試行、401 ではログインし直し）を区別して表示し、取得に失敗している間は未読件数の代わりに「未読件数を取得できません」と表示します。

### IaC

- [イベント処理モジュール](infrastructure/modules/notification_events/main.tf): イベント処理 Lambda、専用の実行ロール、ロググループ（365日）、DLQ（SQS、SSE）、EventBridge ルール（default バス、source・detail-type・`detail.registryId` で絞る）とターゲット、ルールからだけの呼び出し許可、非同期呼び出しの再試行設定。実行ロールが持つのは、`agent-registry:GetRegistryRecord`、補助テーブルの読み取り（`dynamodb:GetItem`・`Query`）、DLQ への `sqs:SendMessage` だけです。補助テーブルへの書き込み（`dynamodb:PutItem`）は、`dynamodb:LeadingKeys` の条件で通知・重複除去の印・処理済みのイベントのパーティションキーに限ります（[条件キーによる項目単位の制御](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/specifying-conditions.html)）。管理情報や操作記録は書けず、Registry の状態変更や descriptor の書き込みの権限も持ちません。
- ルートモジュール [`infrastructure/notification_events`](infrastructure/notification_events/main.tf): `terraform.tfvars.example` をコピーし、リージョン（Registry と同じ）、Lambda の zip、Registry ARN、補助テーブルの名前と ARN を設定します。
- 通知 API の route（`GET /api/notifications`・`POST /api/notifications/read`）は、[管理 handler の route 用モジュール](infrastructure/modules/management_routes/main.tf) で既存の管理 Lambda の統合に割り当てます。管理 Lambda と決裁 Lambda のロールは、既存の `dynamodb:GetItem`・`PutItem`・`Query` で通知と既読位置を読み書きできるため、権限は追加しません。
- 補助テーブルの TTL（属性 `expiresAt`）が有効であることを前提にします（#5 の IaC が持つ）。

### テスト

Seam 1 の Stubber と moto で、EventBridge イベントを入力に、次を検証します（`tests/test_notifications.py`、`tests/test_notification_permissions.py`）。

- 申請・公開承認・却下の宛先、同じ event ID の再配信で Registry を読まないこと、順序が入れ替わったイベントで再取得した状態を1回だけ通知すること
- ポータル外の変更（申請しか記録のない承認、過去の承認の後の時間をおいた承認）が Admin と所有チームに `OUTSIDE_CHANGE` として届き、Curator と他チームに届かないこと、未管理レコードの変化が Admin にだけ届くこと
- 参照先のポータル外の廃止とポータルでの公開停止が、参照先を使う Agent の所有チームに届くこと、公開中の版で参照をやめた Agent に届かないこと
- 廃止依頼と見送りのアプリ内の通知、拒否された依頼で通知が残らないこと
- 既読位置がユーザー単位で、グループの通知を共有すること、既読位置が戻らないこと、本文の検査、90日の TTL と期限切れの除外、Viewer の0件、503・502 の区別
- 対象外のイベントの無視、Registry の一時的な失敗での例外と再配信での処理、削除済みのレコード、ポータル内の作成の `DRAFT` と処理中の状態を通知しないこと、設定不足での起動エラー
- IaC: 実行ロールの権限（書き込みを許すパーティションキーが、通知の補助ストアが書くキーと一致すること）、Lambda・DLQ・ルール・呼び出し許可、ルールの detail-type と handler が受け付ける種別の一致、ルートモジュール、通知 API の route

Seam 2 の Vitest で、ヘッダーの未読件数、一覧を開くまで表示しないこと、約60秒ごとのポーリングと表示をやめたときの停止、既読の本文と更新、0件と取得失敗・準備中（503）・401 の区別、応答の検査を確認します（`frontend/src/tests/notifications.test.ts`）。

実 AWS への反映と、実環境での EventBridge イベントの受信は行っていません。デプロイ後に実環境で、ルールがイベントを受け取ること（`detail.registryId` が ARN の末尾の ID であること）、IAM の境界、TTL による削除を確認してください。
