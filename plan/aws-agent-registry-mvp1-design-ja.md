# AWS Agent Registry を中心とした MVP1 設計

us-east-1 にある既存の AWS Agent Registry 1個を使い、Agent・MCP サーバー・Skill の登録、公開承認、発見を行う管理ポータルを構築する。A2A は Agent の公開プロトコルとして扱う。Viewer はカタログ項目を探せる。Publisher と Curator は同じ画面で申請内容と公開状態を確認できる。Admin は Registry の標準 API と承認状態を中心に運用する。

本書は、2026年11月3日時点のリポジトリ調査と AWS 公式資料をもとに、grilling セッションで合意した内容を反映した設計である。既存 AWS アカウントへの接続、設定変更、デプロイ、実環境での動作確認は実施していない。

用語は [GLOSSARY.md](../GLOSSARY.md) に従う。主要な判断は次の ADR に記録した。

- [ADR-0001 Registry への書き込みはポータル経由に限定する](../docs/adr/0001-registry-writes-only-through-portal.md)
- [ADR-0002 Registry を正本とし、DynamoDB は補助に限定する](../docs/adr/0002-registry-as-source-of-truth.md)
- [ADR-0003 MVP1 は公開 HTTPS で提供し、閉域化と private 用 Registry は見送る](../docs/adr/0003-public-https-without-waf-and-single-registry.md)
- [ADR-0004 CI から AWS へは反映せず、反映はローカルから手動で行う](../docs/adr/0004-no-aws-deployment-from-ci.md)
- [ADR-0010 結果確認中の決裁は、決裁 handler の再送と、決裁した Curator の操作照会で確定する](../docs/adr/0010-settling-uncertain-decisions.md)
- [ADR-0011 結果確認中の操作の回復は、Admin が決裁 handler で行い、Registry の再取得で確定できないときだけ強制解除する](../docs/adr/0011-admin-recovery-operation.md)

## 前提とした事実

以下は利用者から聞き取った内容で、実環境ではまだ確認していない。最初の実装単位で読み取り確認する（「実装の順序」の1）。

| 項目 | 前提 |
|---|---|
| Registry | 新しい `agent-registry` 名前空間にあり、us-east-1、1個 |
| 自動承認 | 無効 |
| 既存 Registry レコード | 少数 |
| 発見用の認可 | M2M（client credentials）の JWT。IdP は Cognito User Pool |
| 環境 | MVP1 は `dev` の1環境のみ |

## 推奨構成

MVP1 の構成要素は次のとおりである。

- React SPA、非公開 S3、CloudFront OAC
- API Gateway HTTP API
- Python Lambda 3個（管理、決裁、イベント処理）
- 補助 DynamoDB 1テーブル
- EventBridge ルール1本

人間ユーザーの認証には、既存の M2M と同じ Cognito User Pool に人間用の公開 SPA client（PKCE）を追加して使う。CI 用には、発見用 M2M とは別の M2M client を追加する。発見用 M2M のクライアントシークレットは既存の Secrets Manager を参照し、保管先がなければシークレットを1つ追加する。

Registry を、Registry レコードの定義と状態の正本にする（ADR-0002）。DynamoDB に置くのは次のものに限る。

- 申請のスナップショット
- 人間の申請者・決裁者
- 操作履歴
- 冪等性と排他制御
- 所有チームと関連参照
- 通知

独自の承認状態は持たない。

| 選択肢 | 得られるもの | 制約と判断 |
|---|---|---|
| Registry 標準承認と Lambda だけ | 最も少ない構成で登録・申請・承認できる | 人間の申請者と決裁者の対応付け、自己承認禁止、申請内容の固定、同時操作の管理ができない |
| 標準承認と補助 DynamoDB 1テーブル | 上記に加え、申請内容と人間の操作を対応付けられる | **採用**。永続ストアは小さいものが1つ増える |
| 現行 Control Plane 全体を継承 | 既存の汎用承認ポリシーと周辺機能を使える | ECS・ALB・VPC Link と独自承認モデルを引き継ぐことになるので採用しない |

単一組織内で、ログインした全員が同じカタログを閲覧できる前提とする。部門ごとに秘匿するマルチテナント構成は対象外とする（ADR-0003）。

## AWS 仕様から決まる境界

### 管理用の IAM と発見用の JWT を分ける

Registry の JWT 設定が適用されるのは、検索、一覧、承認済みレコードの取得、Registry MCP エンドポイントである。登録・更新・申請・決裁を含むコントロールプレーンは、JWT 設定に関係なく IAM 認可が必要になる。既存の M2M JWT を、登録 API や決裁 API の認証に流用しない。[AWS の認可仕様](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-supported-auth-types.html)

| 通信 | 認証と認可 |
|---|---|
| 人間のブラウザから管理 API | Cognito のアクセストークン（PKCE）。HTTP API の JWT authorizer で検証したうえで、Lambda でもロール・所有チームを検査する |
| CI からポータル API | CI 専用 M2M client のアクセストークン（scope `registry-portal/records.write`）。下書きの作成・更新・申請だけができる |
| Lambda から Registry 管理 API | Lambda 実行ロールによる IAM SigV4。`boto3.client("agent-registry-control")` |
| Lambda から既存 JWT Registry の発見 API | 発見用 M2M client で取得したアクセストークンを HTTP Bearer で送る |
| 外部エージェントから Registry MCP | 既存 M2M の認可をそのまま使う。ポータルを経由させる必要はない |

AWS 公式例では、Boto3 による検索は IAM 認可の Registry 用、JWT 認可の Registry の検索は HTTP クライアント用として示されている。したがって「全通信を通常の Boto3 呼び出しだけで行う」構成は、既存の JWT 設定と合わない。管理操作は Boto3 で実装し、JWT の発見操作だけを標準 HTTP クライアントの小さなアダプターにする。既存 Registry の認可方式は変更しない。認可方式と JWT discovery URL は作成後に変更できないためである。[検索の接続例](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-search-records.html)

発見用 M2M トークンは共通のサービス主体のものであり、人間ユーザーごとの権限を Registry に伝えるトークンではない。ポータル側で閲覧権限を検査してから、代理で検索する。

### 新しいサービス名前空間を使う

公式移行ガイドによると、旧 `bedrock-agentcore` 名前空間の Registry は2026年10月30日に提供を終了した。設計と実装は `agent-registry-control`、`agent-registry`、`agent-registry:*`、`arn:aws:agent-registry:...` を基準にする。AgentCore Runtime・Gateway・Identity の名前空間まで一括で置き換えないよう注意する。[公式移行ガイド](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-faq.html)

ドキュメントには旧 CLI や旧 IAM の例も併記されている。コード例をコピーする前に、新サービスの Boto3 モデルと照合する。Lambda に同梱する Boto3 と Botocore は動作を確認した組合せでバージョンを固定し、Lambda ランタイム付属 SDK の更新に左右されないようにする。[管理 SDK](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control.html)

### 公開承認と実行許可を分ける

Registry の承認は、Registry レコードを発見可能にするための公開承認である。Agent の実行、MCP ツールの呼び出し、A2A リクエストを許可する実行許可ではない。画面の文言は「公開承認」に統一し、公開状態と実行環境・認証方式を分けて表示する。実行許可は MVP2 の接続先サービスで扱う。

Registry の検索と一覧は Registry 単位でしか認可されず、レコード単位の IAM Deny では秘匿できない。Registry MCP の search / list ツールも同じである。発見用 M2M を持つクライアントには、Registry 内のカタログ項目がすべて見える。そこでデータ分類は表示用ラベルとして扱い、confidential 相当の Registry レコードは登録時に拒否する（ADR-0003）。[レコード単位の権限制約](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-iam-permissions.html)

## 現状実装から再利用するもの

調査は codebase-memory の Tier 2 で行った。グラフ世代は `2026-11-03T13:43:47Z`。これは実行時の動作を保証するものではない。

フロントエンドの既存コンポーネントは参照するだけにとどめる。`applications/registry_portal/frontend/` に新しい SPA として書き起こし、import による共有や共通パッケージ化はしない。既存コンポーネントは独自承認モデル、`x-user-email`、ID トークンを前提にしているためである。

| 場所 | 確認した現状 | MVP1 への反映 |
|---|---|---|
| [共通 Registry クライアント](../platform/control_plane/backend/src/services/agent_registry_client.py) の49、56、103、162、229行付近 | 単一 Registry への接続、作成、標準承認操作を1か所にまとめている | descriptor の変換と SDK 操作を抜き出す。FastAPI や独自承認キューへの依存は持ち込まない |
| [承認 API](../platform/control_plane/backend/src/api/routes/approval_requests.py) の161、221、241、253行付近 | AWS 更新に失敗しても、独自キューを承認済みに進める経路がある | この成功判定は移植しない |
| [承認ポリシー評価](../platform/control_plane/backend/src/services/approval_policy_engine.py) の86、98、157、183行付近 | 一致するポリシーがなければ自動承認になる | MVP1 は全件手動承認にする |
| [RBAC](../platform/control_plane/backend/src/core/rbac.py) の279、305、353、363行付近 | VIEWER でも登録できる経路がある | アクセストークンと明示的な Publisher 権限に変更する |
| [React のルート](../platform/control_plane/frontend/src/App.tsx) の193、207、291行付近 | Agent・Skills・MCP・A2A・承認の画面がある | 画面構成の参考にする。A2A は Agent に統合する |
| [承認キュー画面](../platform/control_plane/frontend/src/components/approvals/ApprovalQueueLanding.tsx) の33、49、57行付近 | 状態フィルタ、定期更新、理由付き決裁、一括操作がある | 審査待ちを AWS から取得する方式に変える。一括決裁は外す |
| [ブラウザ認証](../platform/control_plane/frontend/src/auth/AuthContext.tsx) の55、68行付近 | ID トークンを使い、未設定時は開発用に認証を通す | PKCE とアクセストークンを使う。未設定はエラーにする |
| [CloudFront](../platform/control_plane/infrastructure/modules/cloudfront/main.tf) の28、54行付近 | S3 OAC と SPA のルーティング復帰処理 | 方針を再利用し、新スタックで所有する |
| [API Gateway](../platform/control_plane/infrastructure/modules/api_gateway/main.tf) の40行付近 | HTTP API から VPC Link・ALB・ECS に接続 | Lambda proxy integration に置き換える |
| [Registry Terraform](../platform/control_plane/infrastructure/modules/agent_registry/main.tf) の79、103、126行付近 | 名前で検索し、なければ作成する。借用した Registry も destroy で削除対象になる | 新スタックでは使わない。既存の ARN・ID を入力として参照する |

既存 Terraform からモジュールを切り出す場合、モジュール削除をそのまま apply すると destroy が走るおそれがある。実際の state と所有権を確認してから、移行手順を別途作る。

## データモデル

画面の入口は Agent・MCP サーバー・Skill の3つとする。A2A は Agent のプロトコル属性として扱い、Agent の一覧ではフィルタ、登録フォームでは descriptor の切り替えとして表示する。

| 種別 | AWS recordType | primary descriptor | 管理する内容 |
|---|---|---|---|
| Agent | `AGENT` | `custom` など | 目的、実行先の参照、認証方式、利用条件 |
| Agent（A2A） | `AGENT` | `a2aAgentCard` | Agent Card、プロトコルのバージョン、公開先、capabilities、skills |
| MCP サーバー | `MCP` | `mcpServer` | server 定義と tools 定義。それぞれ schema version を分ける |
| Skill | `SKILL` | `agentSkillsDefinition` | SKILL.md、リポジトリ、配布物のバージョンと参照先 |

各 Registry レコードの primary descriptor は1個にする。汎用 JSON にすべて詰め込まず、AWS が検証する型を優先する。Agent Card 内の A2A skills と、Skill（パッケージ）は別物として扱う。[対応する型と descriptor](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-supported-record-types.html)

### 共通項目と custom metadata

共通項目は `name`、`displayName`、`description`、`recordVersion`、所有チーム、環境、データ分類、連絡先、利用条件とする。検索や絞込みに使う属性は Registry の custom metadata schema に定義する。schema の変更は Admin が IaC で行い、アプリには設定変更の権限を与えない。[custom metadata schema](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-custom-metadata-schema.html)

| 属性 | 値 | ルール |
|---|---|---|
| `ownerTeam` | Cognito のチームグループ名 | 必須。所有者の判定は補助テーブルを正本とし、custom metadata は検索用の写しとする |
| `environment` | `dev` | 必須。MVP1 で許可するのは `dev` のみで、既定値も `dev`。値が1つのうちは画面の環境フィルタを表示しない |
| `dataClassification` | `public` / `private` | 必須。public は組織外に公開してよい、private は組織内限定。可視性は変えない。confidential 相当は登録を拒否する |

### バージョン

`recordVersion` は申請のたびに Publisher が semver で入力する。公開中の版より大きい値でなければ申請を拒否する。却下後の再申請では同じ番号を使ってよい。

### 関連参照

Agent と、それが使う Skill・MCP サーバーとの関係は、Registry の record ID と `recordVersion` を明示して補助テーブルに保存し、申請のスナップショットに含める。申請時点で参照先がカタログ項目であること（公開承認済みであること）を必須にし、下書きしかない参照先は申請を拒否する。後で参照先が廃止・公開停止された場合は、警告を表示して通知する。MVP1 は関係を管理するだけで、実行時の自動接続は行わない。

### 秘密情報と外部配布物

定義、metadata、SKILL.md に API キー、JWT、パスワード、secret の値を保存しない。管理するのは、接続先の認証情報が置かれている安全な保管先への参照だけである。

Skill の実ファイルを配信する仕組みは MVP1 では作らない。スクリプトや参照資料は既存のリポジトリやパッケージ配布先に置き、固定した commit・version と、取得できれば digest を記録する。AWS の schema 検証は、外部配布物が実在することも安全であることも保証しない。

## 画面と機能の範囲

| 画面 | MVP1 に含める機能 |
|---|---|
| カタログ | カタログ項目の自然言語・キーワード検索、種別・A2A・データ分類による絞込み、ページ単位の一覧 |
| Registry レコード詳細 | 定義、所有チーム、関連参照、接続先の参照、公開状態を表示する。カタログ項目と下書きは区別して表示する |
| 自分のチームの登録 | 下書きの作成、種別ごとのフォームと JSON 入力、編集、申請、却下理由の確認、廃止依頼 |
| 審査待ち | 申請のスナップショット、公開中の版との差分、申請者（人間またはサービス主体）、理由、公開承認・却下、廃止依頼の処理 |
| 通知欄 | ヘッダーに未読件数を出し、一覧から既読にできる。約60秒ごとにポーリングする |
| 操作履歴 | 操作者、対象、内容のハッシュ、操作結果、AWS request ID、時刻 |
| 未管理レコード（Admin） | ポータル外で作られた Registry レコードの一覧と取り込み |
| 接続情報 | 固定された Registry ARN・リージョン、認可方式、接続エラーの表示。設定変更は IaC で行う |

検索で返るのは最大20件のランキング結果である。全件を確認するためのページング一覧とは API を分ける。検索結果が空の場合と、認証・接続に失敗した場合は、画面上で別の状態として表示する。[検索 API の制限](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry/client/search_discoverable_registry_records.html)

MVP1 の承認は1段とし、申請者と最終編集者以外の Curator が1人で決裁する。Admin も自己承認の禁止から除外しない。種別ごとに決裁者を分けること、多段承認、quorum、代理承認、SLA、ポリシーの任意編集、一括決裁、Registry レコードの実行・試験呼び出し、外部 URL の自動クロール、複数 Registry への対応は対象外とする。

## ロールと所有

| 業務ロール | 表し方 | できること |
|---|---|---|
| Viewer | ログインした全員（グループなし） | カタログの閲覧・検索 |
| Publisher | Cognito グループ `registry-publisher`。書き込みには、チームグループ（例: `team-payments`）による所属チームも必要 | 所属チームが所有チームである Registry レコードの下書き作成・編集・申請・廃止依頼 |
| Curator | `registry-curator` | 審査待ちの閲覧、決裁、公開停止、廃止、廃止依頼の見送り |
| Admin | `registry-admin` | 未管理レコードの取り込み、所有チームの変更、回復操作 |
| サービス主体 | CI 専用 M2M client。設定で1つの所有チームに紐付ける | 下書きの作成・更新・申請のみ。決裁はできない |

所有者は個人ではなくチームとする。同じチームの Publisher は誰でも下書きを編集・申請できる。自己承認の禁止は、申請者と最終編集者という個人単位で判定する。

ユーザーの作成とグループの割り当ては Admin が Cognito 側で行う。セルフサインアップは無効にし、ポータルにはユーザー管理画面を作らない。

業務ロールの導出と、操作の認可は分けて扱う。

- **導出**: 業務ロールは Cognito グループだけから導出する。`registry-publisher` を持てば、`team-*` がなくても業務ロールは Publisher になる。`GET /api/me` はこの導出結果を表示する。
- **認可**: Publisher の書き込み操作（下書きの作成・編集・申請・廃止依頼）では、業務ロールが Publisher であることに加えて、対象 Registry レコードの所有チームが呼び出し元の所属チームに含まれることを検査する。所属チームがない Publisher は、どの Registry レコードにも書き込めない（403）。この検査は各操作の Issue（#7、#9、#13）で実装する。

スコープは呼び出せる API の範囲を、グループは業務ロールを表す。人間用 API の audience には人間用 client だけを、CI 用 API にはサービス主体だけを受け入れる。発見用 M2M client はポータル API では一切受け入れない。

### 401 と 403 の使い分け

| 状況 | 応答 | 返す場所 | 理由 |
|---|---|---|---|
| トークンなし、署名不正、期限切れ、別 issuer・audience | 401 | API Gateway の JWT authorizer（`{"message":"Unauthorized"}`。共通 DTO ではない） | 人間の主体として認証できない。ログインし直せば解決しうる |
| ID トークン（`token_use` が access でない）、別 issuer、`sub` なし、authorizer の情報が想定外の形 | 401 `UNAUTHENTICATED` | Lambda（共通 DTO） | 同上。authorizer の設定誤りに備えて再確認する |
| 署名検証を通った正規のトークンだが、人間用 SPA client 以外（別 client、発見用 M2M client）に発行されたもの | 403 `FORBIDDEN` | Lambda（共通 DTO） | ログインし直しても解決しない |
| 認証済みだが、業務ロールや所属チームが操作に足りない | 403 `FORBIDDEN` | Lambda（共通 DTO） | 同上 |

- 有効期限（`exp`）は authorizer が必ず検証し、設定で外せないので、Lambda では再確認しない。issuer・client など設定を誤りうる項目だけを Lambda で再確認する。
- HTTP API には応答を書き換える仕組み（Gateway Responses）がないため、authorizer の 401 は共通 DTO にならない。SPA は、共通 DTO でない 401 も `UNAUTHENTICATED` として扱い、同じ日本語メッセージを表示する。
- SPA が「もう一度ログインする」を案内するのは、401 とログイン（PKCE）の失敗のときだけとする。403、設定エラー、通信エラー、5xx、想定外のエラーでは、ログインし直しても解決しないので案内しない。

## 承認の状態と整合性

### 業務上の状態は AWS 標準を使う

| 現在の状態 | ポータルで許可する操作と次の状態 |
|---|---|
| 未登録 | 作成すると `DRAFT` |
| `DRAFT` | 編集を続ける。申請すると `PENDING_APPROVAL` |
| `PENDING_APPROVAL` | Curator が公開承認すると `APPROVED`、理由を付けて却下すると `REJECTED` |
| `REJECTED` | Publisher が編集すると `DRAFT` に戻る。その後、再申請する |
| `APPROVED` | 編集すると新しい下書きができる。旧版はカタログ項目として公開されたまま |
| 公開停止 | Curator が理由を付けて却下し、発見できなくする。再公開には編集と再申請が必要。廃止依頼は閉じない（廃止するか見送るかの判断を残す）。最新の版が公開中のときだけ行う（二重リビジョンの挙動は未確認） |
| 廃止 | Publisher の廃止依頼、または Curator の判断で、理由の入力と二重確認を経て `DEPRECATED` にする。元に戻せない。未解決の廃止依頼は廃止で解決する |
| 廃止依頼の見送り | Curator が理由を付けて廃止依頼を閉じる。レコードの状態は変えない。廃止依頼の終わり方は、廃止と見送りの2つだけ |

AWS 自体は却下済みからの直接承認を許すが、ポータルでは再申請を必須にする。承認待ちの最中に編集すると新しい下書きになり、元の申請は無効になる。管理 API は最新版を、発見 API は承認済みの版を返すので、画面上で「下書きあり」と「公開中」が同時に成り立つことがある。[ライフサイクルと二重リビジョン](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-record-lifecycle.html)

`CREATING`、`UPDATING`、`CREATE_FAILED`、`UPDATE_FAILED` は AWS 側の処理状態として表示し、処理中の Registry レコードは決裁させない。公開承認の完了と、検索結果への反映は分けて表示する。検索と MCP は結果整合性なので、承認後は管理 API の状態を表示しつつ、発見への反映待ちであることを示す。

既存 Registry の自動承認設定が無効であることを確認する。設定が想定から外れていた場合は、新規申請を止めて Admin に表示する。

### 補助テーブルに保存するもの

| 項目 | 保存内容と用途 | 保持期間 |
|---|---|---|
| Registry レコードの管理情報 | Registry ID、record ID、所有チーム、関連参照、管理下か未管理か | 無期限 |
| 申請のスナップショット | 申請 ID、申請者の `iss` と `sub`（またはサービス主体の client_id）、申請日時、理由、定義・関連参照・metadata、canonical JSON のハッシュ | 無期限 |
| 公開内容のスナップショット | ポータルで公開承認した内容。次回審査の差分表示に使う。取り込み前の既存レコードで元の承認者が不明なら「不明」と表示する | 無期限 |
| 操作記録 | 操作 ID、操作者、対象のハッシュ、開始時刻、結果、AWS request ID。上書きを禁止する条件式付きで追記する | 無期限 |
| 実行制御 | Registry レコード単位の排他、Idempotency-Key とリクエストのハッシュ、結果が確定していない操作 | 確定後に TTL で削除 |
| 通知 | 宛先グループ、種別、対象、本文、EventBridge の event ID | 90日（TTL） |
| 既読状態 | ユーザー単位の既読位置 | 90日（TTL） |

メールアドレスや `x-user-email` を、権限や操作者を判定する根拠にしない。検証済み JWT の主体を使う。編集者が自分で `submittedBy` を書き換えられる API は作らない。

### 内容を確認してから決裁する

1. ポータル内のすべての書き込みは、対象 Registry レコード共通のロックを条件付き書き込みで取得してから行う。新規作成時は、固定した論理名を重複防止キーにする。
2. 申請時には、実際の AWS 上の Registry レコードを読み、内容・バージョン・更新時刻と操作者を記録してから、標準の申請 API を呼ぶ。
3. 決裁時には、Curator が見たスナップショットのハッシュを受け取る。Lambda は現物の状態と内容を再取得し、申請時のハッシュと一致するか確認する。一致しなければ HTTP 409 を返し、決裁しない。
4. 決裁の意図を先に記録してから、AWS の状態更新 API を呼ぶ。成功応答と再取得の結果を確認したうえで、操作成功を記録する。AWS が失敗した場合は、承認済みと表示しない。
5. AWS では成功したのに補助情報の保存に失敗した場合や、タイムアウトで AWS の結果がわからない場合は、操作を「結果確認中」として記録する。その後 AWS の正本を再取得して照合し、確認が取れるまでそのレコードへの新しい書き込みを止める。
   申請だけは例外として、スナップショットを保存した後に申請 API の受け付けを確認できず、現物が内容の一致する `DRAFT` のままなら、照合（`GET /api/operations/{operationId}`）が自動承認設定を確認し直してから申請 API を呼び直す。照合は書き込みを伴う読み取りになるが、止まった操作がロックを持ち続けて編集が409のままになることを防ぐ。照合より先に決裁された場合は申請を成功、内容が申請時と一致しない場合は申請を失敗として確定し、どちらもロックを解除する。
   決裁も同様に止まり得る。決裁の確定の経路（決裁 handler の再送と、決裁した Curator の操作照会）と決裁理由の上限は ADR-0010 に従う。
6. 上の経路で確定できないまま止まった操作（操作の主体の不在、補助ストア障害の継続など）は、Admin の回復操作で Registry を再取得して照合する。照合でも確定できない場合に限り、Admin は理由を付けて操作を失敗として確定し、ロックを解除できる（強制解除）。強制解除は、Registry を再取得でき、AWS が処理中でなく、AWS が受け付けを返した操作でないことを確かめてから行う。実行中の処理に重ねないよう、開始から一定時間（5分）が過ぎた操作だけを対象にする。詳細は ADR-0011 に従う。

これはアプリ内の操作の整合を取る設計であり、AWS と DynamoDB にまたがる分散トランザクションではない。`UpdateRegistryRecordStatus` には expected revision や `If-Match` に相当するパラメーターがない。そこで ADR-0001 に従い、ポータルの Lambda 以外の書き込み経路を IAM で Deny する。break-glass 用管理ロールによる直接の変更は、通知と監査で検出する。[決裁 API の引数](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/update_registry_record_status.html)

SDK に `clientToken` がある操作は、同じ操作 ID を使って再試行する。決裁 API に存在しない冪等性引数を前提にしない。ロックの期限が切れたことを理由に、結果が確定していない処理の上から別の書き込みをしない。ロックを外せるのは、操作の確定と、上記6の Admin の強制解除だけである。DynamoDB の TTL は、ロックを取ってよいかの判定には使わず、確定済みの実行制御項目を片付けるためだけに使う。

## 通知

Registry が EventBridge の default バスに送るイベントを、ルール1本でイベント処理用 Lambda に流す。[Registry EventBridge イベント](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-eventbridge.html)

| 受け取るイベント（`aws.agent-registry`） | 通知先 |
|---|---|
| `Registry Record State changed to Pending Approval` と、廃止依頼（アプリ内で発生） | Curator 全員 |
| `... to Approved`、`... to Rejected`、`... to Deprecated` | 所有チームの Publisher 全員 |
| 廃止依頼の見送り（アプリ内で発生。AWS の状態を変えないためイベントは出ない） | 所有チームの Publisher 全員 |
| 参照先の Skill・MCP サーバーの廃止・公開停止 | その参照先を使う Agent の所有チーム |
| 操作記録と照合できない状態変化（ポータル外の変更） | Admin 全員 |

イベントに含まれるのは record ID だけで、操作者は含まれない。そのため Lambda は Registry レコードを再取得し、補助テーブルの操作記録と照合して「ポータル内の変更か、外の変更か」を判定する。EventBridge は重複して配信したり順序が入れ替わったりするので、event ID で重複を除き、状態は常に再取得した値を使う。

通知はグループ単位で1件だけ作り、既読はユーザー単位で管理する。メール、SNS、WebSocket は使わない。

## 最小インフラとセキュリティ

| 要素 | 構成と制約 |
|---|---|
| リージョン | us-east-1。検索処理は米国内の別リージョンで行われる可能性がある |
| SPA 配信 | S3 の Block Public Access を有効にし、CloudFront OAC と distribution ARN に限定した bucket policy で配信する。S3 website endpoint は使わない |
| HTTPS | CloudFront と HTTP API の標準ドメインを使う。独自ドメインと Route 53 は使わない |
| SPA 認証 | 既存 Cognito User Pool の人間用公開 client で、Authorization Code + PKCE。secret は持たない。M2M client とは分ける |
| API の入口 | HTTP API の JWT authorizer で、issuer、audience（client_id）、有効期限、route ごとの scope を検証する。業務ロールと所有チームは Lambda で検査する |
| 管理 Lambda | 発見の代理、Registry レコードの取得・作成・更新・申請、廃止依頼、通知 API。Registry の状態を変更する権限は持たない |
| 決裁 Lambda | 公開承認、却下、公開停止、廃止、廃止依頼の見送り、取り込み、操作の回復。descriptor の作成・更新権限は持たない |
| イベント処理 Lambda | EventBridge から起動される。Registry の読み取りと、補助テーブルの通知への書き込みだけを行う |
| 補助 DynamoDB | 1テーブル、オンデマンド、暗号化、PITR、削除保護 |
| Secrets Manager | 既存の発見用 M2M secret の ARN を参照する。取得できるのは管理 Lambda だけ。トークンは有効期限を考慮してメモリー上で再利用し、401 時の再取得は回数を制限する |
| ログ | CloudWatch Logs は365日保存。request ID、操作 ID、主体の識別子、結果を記録する。JWT、secret、定義の全文、SKILL.md の全文は記録しない |
| 監査 | 操作履歴は補助テーブルに無期限で保存する。CloudTrail は既存の組織 trail を使う。WORM（S3 Object Lock）は付けない |
| ネットワーク | Lambda は VPC に入れず、公開 AWS API へ TLS で接続する。VPC、NAT Gateway、ALB、ECS、VPC Link、WAF は追加しない（ADR-0003） |

SPA のトークンはメモリー上だけで保持し、ページを再読み込みしたら SSO にリダイレクトし直す方式でよい。API には ID トークンではなく、scope を持つアクセストークンを渡す。Lambda でも `token_use=access` を確認する。

route ごとの scope 検証を有効にするには、Cognito のリソースサーバーにポータル用のカスタム scope を定義し、人間用 client に許可したうえで、SPA の `VITE_COGNITO_SCOPES` にその scope を含める必要がある。#3 の時点では SPA の既定値は `openid` だけで、JWT authorizer の scope 検証はまだない。カスタム scope の定義と SPA 設定の更新は、JWT authorizer を作る #5 で行う。[PKCE](https://docs.aws.amazon.com/cognito/latest/developerguide/using-pkce-in-authorization-code.html)、[HTTP API JWT authorizer](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html)

Lambda を3個に分けるのは、IAM の職責を分けるためである。同じ Python パッケージからビルドしてもよいが、handler と実行ロールはそれぞれ別にする。`UpdateRegistryRecordStatus` は公開承認・却下・廃止に共通の IAM action なので、どの遷移を許すかは決裁 Lambda のコマンドごとに検査する。クライアントから AWS の `status` を任意に受け取る API は作らない。

実行ロールの対象は既存 Registry とそのレコードの ARN に絞り、必要な action だけを列挙する。Registry 自体の作成・削除・設定変更、レコードの物理削除、`iam:PassRole` は与えない。[Registry IAM action と resource](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-iam-permissions.html)

Lambda は API Gateway からだけ起動できるよう Lambda permission を絞り、Function URL は作らない。CORS は本番 SPA の origin だけに限定するが、CORS を認可の代わりにはしない。SPA には CSP とセキュリティヘッダーを設定する。SKILL.md を表示するときは、raw HTML やスクリプトを実行しない。API や認証のエラー応答を、SPA の `index.html` に差し替えない。

MVP1 では URL を定義の一部として登録するだけで、Lambda が登録された任意の URL を取得することはない。URL の検査は文法と許可スキームに限り、「接続試験済み」とは表示しない。

## デプロイと CI

ADR-0004 に従い、GitHub Actions では AWS への反映を一切行わない。

| 区分 | 内容 |
|---|---|
| GitHub Actions | pytest、Flake8、Black、mypy、ESLint、Vitest、`npm run typecheck`、`terraform fmt -check`、`terraform validate`。AWS 認証情報と OIDC ロールは持たせない |
| AWS への反映 | オペレーターがローカル端末から、IAM Identity Center の短期認証情報で `scripts/deploy.sh` を手動実行する。長期アクセスキーは使わない |
| デプロイ権限 | 専用の permission set `RegistryPortalDeployer`。Registry レコードの書き込み系操作と `UpdateRegistry` / `DeleteRegistry` は Deny する。break-glass 用の管理ロールとは別にする |
| Terraform state | S3 backend（バージョニング、暗号化、パブリックアクセスブロック）と S3 ネイティブのロック（`use_lockfile = true`、Terraform 1.10 以上）。state 用バケットは `bootstrap/` で一度だけ作成し、アクセスは `RegistryPortalDeployer` だけに許可する |
| `scripts/deploy.sh` | plan → 目視確認 → apply → SPA を S3 に同期 → CloudFront のキャッシュ無効化。plan に既存 Registry への update / destroy が含まれていたら中断する |

既存 Registry は Terraform の resource として持たず、ARN・ID・リージョンを変数として与える。

## API と実装単位

新しい小規模アプリとして `applications/registry_portal/` に分離する。現行の Control Plane 全体を Lambda 化することはしない。

| パス | 責務 |
|---|---|
| `backend/handlers/management.py` | API Gateway からの入力、認可、Registry レコードの管理・発見・通知 |
| `backend/handlers/curation.py` | 決裁コマンドの受付、認可、結果の確認、取り込み、回復 |
| `backend/handlers/events.py` | EventBridge イベントの受信、照合、通知の作成 |
| `backend/domain/` | 種別と descriptor のモデル、状態遷移、承認ルール、スナップショット |
| `backend/services/` | Registry 管理用 Boto3、JWT 発見用 HTTP、補助 DynamoDB、M2M トークンの取得 |
| `frontend/` | React SPA（新たに書き起こす） |
| `infrastructure/` | 専用 Terraform。既存 Registry は作成しない |
| `scripts/deploy.sh` | ローカル反映用のスクリプト |

| API | 処理 | 呼び出せる主体 |
|---|---|---|
| `GET /api/catalog` | 発見 API を使ったカタログ項目の一覧。nextToken を中継する | 人間 |
| `POST /api/catalog/search` | 自然言語検索。許可した metadata filter だけを組み立てる | 人間 |
| `GET /api/catalog/{id}` | カタログ項目の詳細。batch-get に ID を1件渡して取得する | 人間 |
| `GET /api/records` | 管理用の一覧。業務ロールと所有チームで絞り込む | 人間 |
| `POST /api/records` | 下書きの作成。Idempotency-Key を必須にする | 人間、サービス主体 |
| `GET /api/records/{id}` | 管理中の最新版を取得する | 人間、サービス主体 |
| `PATCH /api/records/{id}` | 種別ごとの編集。expected content hash を必須にする | 人間、サービス主体 |
| `POST /api/records/{id}/submit` | スナップショットの保存と標準の申請 | 人間、サービス主体 |
| `POST /api/records/{id}/deprecation-requests` | 廃止依頼 | Publisher |
| `GET /api/reviews` | AWS の承認待ちと廃止依頼に補助情報を結合して返す。状態は AWS を正本とする | Curator |
| `POST /api/reviews/{id}/approve` | 理由とスナップショットのハッシュを検査して公開承認する | Curator |
| `POST /api/reviews/{id}/reject` | 理由を必須にして却下する | Curator |
| `POST /api/records/{id}/suspend` | 公開停止。却下で発見できなくすることを明示する | Curator |
| `POST /api/records/{id}/deprecate` | 元に戻せない廃止であることを明示し、二重確認を経て実行する | Curator |
| `GET /api/records/{id}/lifecycle` | 公開管理の照会。状態・公開中の版・未解決の廃止依頼・実行できる操作（できない理由）を返す。決裁 Lambda が処理する | Curator |
| `POST /api/records/{id}/deprecation-requests/decline` | 廃止依頼の見送り。理由を必須にし、レコードの状態は変えない。決裁 Lambda が処理する | Curator |
| `POST /api/records/{id}/adopt` | 未管理レコードの取り込みと所有チームの割り当て | Admin |
| `GET /api/operations/{id}` | 操作結果、結果確認中の状態、回復結果 | 人間 |
| `GET /api/records/{id}/recovery` | ロックを持つ未確定の操作の概要（種別・主体・開始時刻・回復できるようになる時刻）。決裁 Lambda が処理する | Admin |
| `POST /api/records/{id}/recovery` | Registry を再取得して照合し、確定できなければ理由を付けて強制解除する。決裁 Lambda が処理する | Admin |
| `GET /api/records/{id}/history` | 操作と審査の履歴 | 人間 |
| `GET /api/notifications` | 自分宛ての通知と未読件数 | 人間 |
| `POST /api/notifications/read` | 既読位置の更新 | 人間 |

管理 API のページングには AWS の nextToken を使う。所有チームでの絞込みによってページが空になった場合も、nextToken は保持する。全件数を出すためや複合検索のために、リクエストのたびに全レコードを走査することはしない。外部から受け取る token は長さと形式を制限し、Registry ID や endpoint URL を利用者から受け取らない。

作成と更新で SDK の payload を共用しない。新 API の更新には `optionalValue` という patch 用のラッパーがあるので、明示的に変換する処理を設ける。AWS のレスポンスをそのまま SPA に返さず、日本語のエラーメッセージと共通の DTO に変換する。[作成 API](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/create_registry_record.html)、[更新 API](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/update_registry_record.html)

## MVP2 への拡張

| 連携先 | MVP2 で追加するもの | MVP1 から引き継ぐ境界 |
|---|---|---|
| AgentCore Runtime | 実行環境の一覧と状態の取得、管理対象への登録、必要に応じたデプロイ連携 | Registry の record ID と Runtime ARN を関連付ける。公開承認とデプロイの承認は分ける |
| AgentCore Gateway | MCP 接続先やツール定義の取り込み、同期結果と差分の表示 | 同期によって定義が変わる場合は下書きを作り、再申請を求める |
| AgentCore Identity | 実行時の credential provider、ユーザーやサービス主体の認証連携 | ポータルの SSO、発見用 M2M、実行時の認証情報はそれぞれ分けて扱う |
| AgentCore Policy | 呼び出し時のポリシー適用と実行許可 | 公開承認を実行許可の代わりに使わない |
| Observability | 実行結果、接続状態、監査へのリンクの表示 | 変化し続けるテレメトリーを Registry に複製しない |
| private 用 Registry | 所有チームだけが発見できる Registry の追加 | ADR-0003 を見直す |
| 環境の追加 | `environment` に stg / prd を追加 | 値を増やすだけで済み、既存レコードの一括書き換えは不要 |

MVP1 で接続先の ARN と関連参照を明示的に保存しておけば、拡張の入口としては十分である。まだ使わない Runtime・Gateway・Policy の AWS リソースや、広い IAM 権限を先に作ることはしない。

## 実装の順序と受入条件

各単位で動作を確認しながら進める。着手時には、リポジトリの規約に沿って Issue を作成する。

1. **既存接続を確定する。** 「前提とした事実」の各項目を、読み取り操作だけで確認する。あわせて、既存レコードの数、作成経路、Terraform の所有権を記録する。
2. **閲覧経路を作る。** Cognito PKCE、HTTP API、JWT 発見アダプターをつなぎ、既存のカタログ項目を SPA で表示する。
3. **登録経路を作る。** 3つの入口（A2A を含む）から定義を作成・編集・申請し、対応する AWS descriptor と状態を確認する。CI 用のサービス主体からの申請も確認する。
4. **決裁・通知・監査を作る。** 別のユーザーで公開承認・却下・公開停止・廃止を行い、申請内容の固定、自己承認の拒否、失敗時の回復、通知の配信を確認する。
5. **IaC と配信を整える。** state backend、permission set、`scripts/deploy.sh` を用意する。plan に既存 Registry の作成・変更・削除が含まれないことを確認する。

| 分類 | 受入条件 |
|---|---|
| 権限 | トークンなし、期限切れ、別 issuer、別 client、ID トークン、発見用 M2M のトークンを拒否する。Viewer による登録、Publisher による決裁、他チームの下書きの取得、所属チームのない Publisher による書き込み、サービス主体による決裁を拒否する。認証できないトークンは 401、正規だが許可しない呼び出し元は 403 とする |
| 主体 | `x-user-email` を偽装しても、申請者や決裁者は変わらない。申請者や最終編集者による自己承認を拒否する |
| 種別 | Agent・A2A・MCP サーバー・Skill の定義を AWS が受理する。不正な schema version と種別の組合せには日本語のエラーを表示する |
| 分類 | confidential 相当の登録、`dev` 以外の environment、公開中の版以下の recordVersion を拒否する |
| 関連参照 | カタログ項目になっていない参照先を含む申請を拒否する。参照先が廃止されたら警告と通知を出す |
| 版 | 公開中の Registry レコードを編集しても旧版は公開されたままで、下書きは管理画面にだけ見える。再承認後に新しい内容へ切り替わる |
| 同時操作 | 編集中の承認、承認と却下の競合、ボタンの重複クリックで、意図しない公開や履歴の二重記録が起きない |
| 失敗 | AWS の403・429・5xx、通信タイムアウト、AWS 成功後の DynamoDB 障害を注入しても、誤って承認成功と表示しない |
| 回復 | Lambda の再起動後も結果が確定していない操作を取得でき、Registry と照合して回復できる。確定するまで別の書き込みを拒否する |
| 通知 | 申請、決裁、廃止、ポータル外での変更が、それぞれ正しいグループに1回だけ届く。90日後に削除される |
| 発見 | トークンの更新、検索とページング一覧、結果が空の場合と障害の区別、承認後の検索反映待ちの表示を確認する |
| 既存レコード | 未管理レコードは、取り込まれるまで Publisher が更新できない |
| 配信 | S3 から直接取得しようとすると拒否される。SPA の深い URL で再読み込みできる。API の401・403・404 が HTML の成功応答に置き換わらない |
| IaC | 既存 Registry の destroy が含まれない。実行ロールとデプロイ用の permission set に、Registry 本体の作成・削除や物理レコードの削除の権限がない。CI に AWS 認証情報がない |

状態遷移と認可は単体テストで確認し、SDK の payload は Botocore モデルと Stubber で検証する。JWT 検索、公開中の版と下書き、エラー回復、IAM の境界、EventBridge 通知は、テスト用の Registry レコードを使った結合試験で確認する。React は型検査と build を通したうえで、実ブラウザで登録から申請、別ユーザーによる承認、通知までを確認する。

## 実装前に扱う現状の課題

以下は調査で見つかった課題を追跡するためのローカル記録である。GitHub Issue はまだ作成していない。

- [ ] `REG-01`: 既存の承認 API が、AWS の更新に失敗した後も独自キューを承認済みにする経路を改める。
- [ ] `REG-02`: 借用した既存 Registry を destroy で削除してしまう Terraform の所有権を分離する。
- [ ] `REG-03`: 申請者と決裁者を、自由入力のヘッダーではなく検証済みの主体から取得する。
- [ ] `REG-04`: 承認ポリシー未設定時の自動承認と、本番環境で開発用の認証を通してしまう処理を、MVP1 に持ち込まない。
