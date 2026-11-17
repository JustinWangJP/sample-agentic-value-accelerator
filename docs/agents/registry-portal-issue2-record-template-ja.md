# Issue #2 の作業記録テンプレートと再確認の手順

[Issue #2](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/2) の確認と準備の結果を、Issue のコメントとして残すための書式です。あわせて、別の人（または後日の自分）が同じ手順で結果を再現し、記録と一致するかを確かめる方法を示します。

作業の手順そのものは、次の2つの手順書にあります。この文書には手順を書きません。

1. [個人 AWS アカウントで IAM ユーザーとロールを作成する](registry-portal-iam-setup-personal-account-ja.md)
2. [既存の AWS Agent Registry を確認し、break-glass の手順を決める](registry-portal-existing-registry-check-ja.md)

以下、前者を「IAM 手順」、後者を「確認手順」と呼びます。

## 1. 記録の決まり

### 1 回の作業で 1 コメント

- 作業を 1 回行うごとに、Issue #2 にコメントを 1 つ追加します。やり直したときは新しいコメントを追加し、前のコメントは編集しません。件数などは時間とともに変わるため、取得日時ごとの記録を残します。
- 値はコマンドの出力からコピーします。手で打ち直しません。
- どのコマンドで得た値かを「手順」列に書きます。再確認する人は、その手順のコマンドを実行して比べます。
- 記録の最初に、使った手順書の版（コミット ID）を書きます。手順書が変わると、同じ結果にならないことがあるためです。

```bash
git log -1 --format='%h %cs' -- docs/agents/
```

### 値の扱い

このリポジトリは公開されています。Issue に書いた内容は誰でも読めます。

| 区分 | 対象 | Issue への書き方 |
| --- | --- | --- |
| 記録しない | パスワード、アクセスキー、MFA のシークレット、client secret、アクセストークン、非公開レコードの本体（descriptor の中身）、`describe-user-pool-client` の出力全体 | 書かない |
| 伏せて記録する | AWS アカウント ID（ARN に含まれるものも） | 末尾 4 桁だけ残し、`********3333` のように書く |
| そのまま記録する | Registry ID、User Pool ID、client_id、secret の名前、ロール名、件数、設定値（`true`/`false` など）、日時 | そのまま書く |

伏せる前の完全な値は、手元の `dev.tfvars`（`.gitignore` の対象）か、リポジトリの外の作業メモに残します。再確認する人は AWS アカウントにサインインできるので、完全な値はコマンドの出力で確かめられます。

## 2. 記録テンプレート

次のブロックをコピーし、`<...>` を実際の値に置き換えて Issue #2 にコメントします。確認できた項目の `[ ]` を `[x]` にします。行わなかった項目は `[ ]` のまま「未実施」と理由を書きます。

```markdown
## Issue #2 作業記録（<YYYY-MM-DD>）

### 作業環境

| 項目 | 値 | 手順 |
| --- | --- | --- |
| 作業者 | <GitHub ユーザー名> | — |
| 作業日時（UTC） | <開始> 〜 <終了> | — |
| 手順書の版 | <git log -1 の出力> | 記録の決まり |
| 実行環境 | <CloudShell または ローカル端末> | 確認手順 0 |
| AWS CLI のバージョン | <aws --version の出力> | 確認手順 0 |
| 引き受けたロール | <get-caller-identity の Arn（アカウント ID は伏せる）> | 確認手順 0 |

### IAM（受け入れ条件 5）

- [ ] `registry-operator` の ARN: <ARN>。MFA: <設定済み／未設定>
- [ ] `RegistryPortalReadOnly` の ARN: <ARN>
- [ ] `RegistryPortalDeployer` の ARN: <ARN>
- [ ] `RegistryPortalBreakGlass` の ARN: <ARN>
- [ ] 拒否設定のシミュレーション（IAM 手順 8）: `explicitDeny` <件数>/12、読み取りの `allowed` <件数>/2。`REGISTRY_ID` は <実際の ID／仮の値> で実行した

### Registry（受け入れ条件 1・2）

- [ ] 名前: <name>、ID: <registryId>、状態: <status>（確認手順 1・2）
- [ ] ARN: <registryArn>。`agent-registry` 名前空間・us-east-1 であること: <はい／いいえ>（確認手順 2）
- [ ] `autoApprovalRules`: <[] など>。自動承認: <無効／有効>（確認手順 2）
- 複数の Registry から選んだ場合の理由: <理由または「該当なし」>

### custom metadata schema（PR #20 からの引き継ぎ）

- [ ] 共通スキーマ（`defaultSchema`）: <未設定／設定あり>（確認手順 3）
- [ ] `AGENT` 用スキーマ（`recordTypeSchemaOverrides`）: <未設定／設定あり>（確認手順 3）

| フィールド | 定義場所（common／agent） | 型 | 必須 |
| --- | --- | --- | --- |
| <名前> | <common> | <string> | <はい／いいえ> |

- [ ] `isA2A`: <なし／あり（定義場所、型、必須かどうか）>

### 発見用 M2M（受け入れ条件 3）

- [ ] 認可タイプ: <CUSTOM_JWT／AWS_IAM>（確認手順 4）
- [ ] `discoveryUrl` から読み取った IdP: <Cognito User Pool／その他>、リージョン: <リージョン>、User Pool ID: <ID>
- [ ] Registry の `allowedClients`: <値>、`allowedAudience`: <値>、`allowedScopes`: <値または「Registry 側の scope 制限なし」>、カスタムクレームのルール数: <数>
- [ ] 発見用 client_id: <ID>。`oauthEnabled`: <値>、`flows`: <値>、`hasSecret`: <値>、`scopes`: <値>
- [ ] Cognito ドメイン: <domain または customDomain>
- [ ] secret の名前: <Name>、ARN: <ARN>、`kmsKeyId`: <値または「なし（AWS 管理キー）」>、`deletedDate`: <なし>
- [ ] secret と client_id を対応付けた根拠: <名前、説明、タグのどれで判断したか>
- [ ] User Pool が us-east-1 にあること: <はい／いいえ>

### レコード（受け入れ条件 4）

- 取得日時（UTC）: <date の出力>（確認手順 5）
- [ ] `total`: <数>、`uniqueIds`: <数>、`autoDetected`: <数>、`schemaNonCompliant`: <数>

| 種別 | 状態 | 件数 |
| --- | --- | --- |
| <AGENT> | <APPROVED> | <数> |

| 作成経路 | 件数 | 根拠 |
| --- | --- | --- |
| AWS の自動検出 | <数> | `createdByAutoDetection` |
| AWS CLI | <数> | CloudTrail のイベント ID: <ID, ...> |
| SDK を使うスクリプトやアプリ | <数> | <根拠> |
| Terraform | <数> | <根拠> |
| コンソール | <数> | <根拠> |
| 不明 | <数> | 参照した期間: <期間>、資料: <イベント履歴／証跡名／なし> |
| **合計** | <`total` と同じ数> | |

- 証跡（trail）: <なし／名前と S3 バケット>

### Agent Card（PR #20 からの引き継ぎ）

- 取得日時（UTC）: <date の出力>（確認手順 6）
- [ ] 公開中の `AGENT`: <数>、ID 重複: <0>
- [ ] Agent Card あり: <数>、Agent Card なし: <数>（なしの分は #16 で補完する対象）

### Terraform に渡す値（受け入れ条件 7）

| 変数 | 値 |
| --- | --- |
| `aws_account_id` | <伏せた値> |
| `registry_arn` | <ARN> |
| `cognito_user_pool_id` | <ID> |
| `cognito_domain` | <https://...> |
| `identity_providers` | <["COGNITO"] など> |
| `team_names` | <["payments"] など> |
| `discovery_token_url` | <https://.../oauth2/token> |
| `discovery_client_id` | <ID> |
| `discovery_client_secret_arn` | <ARN> |
| `discovery_kms_key_arn` | <null または ARN> |
| `discovery_scope` | <scope または ""> |

- [ ] セルフサインアップ: `adminCreateOnly` = <true／false>（確認手順 7）
- [ ] 名前の重なり（確認手順 7）: グループ <なし／名前>、リソースサーバー <なし／名前>、client <なし／名前>、IAM ロール <なし／名前>

### break-glass（受け入れ条件 6・PR #20 からの引き継ぎ）

- [ ] 管理ロール: <`RegistryPortalBreakGlass` の ARN>
- [ ] 利用者: <名前>、承認者・監査担当者: <名前>
- [ ] 利用手順の保管先: 確認手順 8（<手順書の版>）と、利用ごとに起票する Issue
- [ ] `isA2A` をポータル外で補完するときの制約を確認した（確認手順 8「ポータル外で `isA2A` を補完する場合」）

### 前提との差分（受け入れ条件 8）

- [ ] 親 Issue #1 へのコメント: <コメントの URL>
- 差分の一覧: <項目名, ...>
```

## 3. 再確認の手順

記録が正しいかを、別の人（または後日の自分）が確かめる手順です。確認手順と同じく、AWS は変更しません。

### 準備

1. 記録の「手順書の版」のコミットを取得し、その版の手順書を開きます。

   ```bash
   git fetch origin
   git show <コミット ID>:docs/agents/registry-portal-existing-registry-check-ja.md | less
   ```

   手順書が更新されている場合は、記録の版の手順で実行します。最新版との違いで結果が変わる場合は、その違いを再確認の結果に書きます。

2. 確認手順 0 のとおり、`RegistryPortalReadOnly` に切り替えて CloudShell を開きます。`aws --version` を実行し、記録のバージョンと違えば書き留めます。
3. 記録の値を変数に設定します。確認手順の途中で設定する変数と同じ名前です。

   ```bash
   set -o pipefail
   export AWS_REGION=us-east-1
   REGISTRY_ID='記録の Registry ID'
   COGNITO_REGION='記録の User Pool のリージョン'
   USER_POOL_ID='記録の User Pool ID'
   DISCOVERY_CLIENT_ID='記録の client_id'
   SECRET_ARN='記録の secret の ARN（アカウント ID は実際の値に戻す）'
   ```

### 比べ方

確認手順の 1〜7 のコマンドを、上の変数を使ってそのまま実行し、出力を記録と比べます。手順 1 の一覧と、手順 4 の Secrets Manager の一覧は、記録の値が含まれていることだけを確かめます。

| 区分 | 項目 | 一致しないとき |
| --- | --- | --- |
| 完全に一致するはず | Registry の ID・ARN・状態、`autoApprovalRules`、スキーマのフィールドと `isA2A`、認可タイプ、`discoveryUrl`、`allowedClients`・`allowedAudience`・`allowedScopes`、client の設定、ドメイン、secret の ARN と `kmsKeyId`、`adminCreateOnly`、名前の重なりの結果 | 記録のあとに設定が変わった可能性があります。CloudTrail のイベント履歴で、記録の日時以降の `UpdateRegistry`、`UpdateUserPoolClient`、`UpdateUserPool`、`UpdateSecret` などを探し、変更した主体と日時を書きます |
| 時間とともに変わる | レコードの件数、種別・状態ごとの件数、公開中の `AGENT` と Agent Card の件数 | 記録の取得日時以降に作成・更新・状態変更されたレコードで説明できるかを確かめます。CloudTrail のイベント履歴で `CreateRegistryRecord`、`UpdateRegistryRecord`、`UpdateRegistryRecordStatus`、`DeleteRegistryRecord` を記録の日時以降に絞って数え、差分と合うかを書きます |
| 判断を含む | 作成経路の分類、secret と client_id の対応付け、`team_names`、break-glass の担当者 | 記録の「根拠」の列をたどり、同じ判断になるかを確かめます。違う判断になる場合は理由を書きます |

確認手順 6 は発見用 M2M の client secret を使うため、再確認する人にも `cognito-idp:DescribeUserPoolClient` の権限が必要です。`RegistryPortalReadOnly` にはこの権限があります。

IAM の記録は、IAM 手順 8 のシミュレーションをもう一度実行して確かめます。このとき `REGISTRY_ID` には、仮の値ではなく記録の Registry ID を使います。

### 結果の書き方

再確認の結果は、元の記録のコメントへの返信として、次の形で Issue #2 に追加します。

```markdown
## Issue #2 再確認（<YYYY-MM-DD>）

- 対象の記録: <元のコメントの URL>
- 再確認した人: <GitHub ユーザー名>、日時（UTC）: <日時>
- 手順書の版: <コミット ID>、AWS CLI: <バージョン>
- 結果: <すべて一致／差分あり>

| 項目 | 記録の値 | 再確認の値 | 説明 |
| --- | --- | --- | --- |
| <差分があった項目だけ> | <値> | <値> | <変更のイベント ID、取得日時の違いなど> |
```

終わったら、確認手順 9 の「後片付け」のとおり CloudShell の作業ファイルを削除します。

## 関連文書

- [個人 AWS アカウントで IAM ユーザーとロールを作成する](registry-portal-iam-setup-personal-account-ja.md)
- [既存の AWS Agent Registry を確認し、break-glass の手順を決める](registry-portal-existing-registry-check-ja.md)
- [Registry ポータルの Terraform ガイド](registry-portal-terraform-guide-ja.md)
