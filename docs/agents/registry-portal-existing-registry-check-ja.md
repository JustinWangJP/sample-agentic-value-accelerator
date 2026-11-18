# 既存の AWS Agent Registry を確認し、break-glass の手順を決める

[Issue #2](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/2) のチェックリストのうち、IAM ユーザーとロールの作成以外の項目を進める手順書です。個人 AWS アカウントで、[IAM ユーザーとロールの作成手順](registry-portal-iam-setup-personal-account-ja.md)を済ませていることを前提にします。

確認作業では AWS を変更しません。読み取り用ロール `RegistryPortalReadOnly` と CloudShell を使います（例外は、手順 7 で KMS キーのエイリアスから ARN を調べる場合だけです）。手順 8 の break-glass だけは、手順を決めて記録するところまで行い、実際の変更はしません。

IAM Identity Center は使いません。Identity Center のアカウントインスタンスは permission set に対応していないため、設計書の permission set `RegistryPortalDeployer` は同じ名前の IAM ロールで代替します（[AWS ドキュメント](https://docs.aws.amazon.com/singlesignon/latest/userguide/account-instances-identity-center.html)）。

ここで確認した値は、ポータルを配信するときに Terraform の変数として使います。手順 7 で、[Terraform ガイド](registry-portal-terraform-guide-ja.md)の `dev.tfvars` に設定する値の形にまとめます。

| Issue #2 のチェック項目 | 手順 |
| --- | --- |
| Registry が `agent-registry` 名前空間・us-east-1 にあること、ARN と ID | [手順 1](#1-registry-を特定する) |
| Registry の自動承認が無効であること | [手順 2](#2-registry-の詳細と自動承認を確認する) |
| custom metadata schema の現状（`AGENT` のフィールド、`isA2A` の有無） | [手順 3](#3-custom-metadata-schema-を確認する) |
| 発見用 M2M の IdP、User Pool ID、client_id、secret の保管先 ARN、scope | [手順 4](#4-発見用-m2m-の-cognito-設定と-secret-の保管先を確認する) |
| 既存レコードの件数と作成経路 | [手順 5](#5-レコードの件数と作成経路を調べる) |
| 公開中の `AGENT` のうち Agent Card あり・なしの件数 | [手順 6](#6-公開中の-agent-を-agent-card-の有無で数える) |
| （配信の準備）Terraform に渡す値と、ポータル用の名前が既存の資源と重ならないこと | [手順 7](#7-terraform-に渡す値をまとめ名前の重なりを確認する) |
| break-glass 用の管理ロールと、利用手順の担当者 | [手順 8](#8-break-glass-の担当者と利用手順を決める) |
| ポータル外で `isA2A` を補完するときの break-glass の手順と担当者 | [手順 8](#8-break-glass-の担当者と利用手順を決める) |
| permission set `RegistryPortalDeployer`（IAM ロールで代替済み） | [IAM の作成手順](registry-portal-iam-setup-personal-account-ja.md#10-issue-2-に記録する) |
| 前提と異なる点を親 Issue にコメント | [手順 9](#9-記録して前提との差分を報告する) |

結果は [Issue #2 の作業記録テンプレート](registry-portal-issue2-record-template-ja.md)の書式で記録します。同じ文書に、別の人が結果を再現して確かめる手順もあります。

画面の表記は「日本語（English）」の形で示します。コンソールの日本語表記は更新で変わることがあります。

## 0. 作業の準備

1. コンソールサインイン URL から `registry-operator` でサインインします。
2. 右上のユーザー名 → **ロールの切り替え**（Switch role）で `RegistryPortalReadOnly` に切り替えます。
3. 右上のリージョンを **米国東部（バージニア北部）**（us-east-1）にします。
4. コンソール上部のツールバーにある CloudShell のアイコンを選び、CloudShell を開きます。
5. 次のコマンドを実行します。`Arn` に `assumed-role/RegistryPortalReadOnly/` が含まれることと、`list-registries` がエラーにならないことを確認します。`aws --version` の出力と `Arn` は、作業記録の「作業環境」に書きます。

```bash
set -o pipefail
export AWS_REGION=us-east-1
aws --version
aws sts get-caller-identity
aws agent-registry-control list-registries --output json > /dev/null && echo "agent-registry-control: OK"
```

`agent-registry-control` が `Invalid choice` になる場合は、CloudShell の AWS CLI が古い状態です。ローカル端末で `registry-read` プロファイルを使い、各コマンドに `--profile registry-read` を付けて実行します（AWS CLI 2.40.3 で動作するコマンドです）。

以降のコマンドは、同じ CloudShell のセッションで続けて実行します。途中で設定する変数（`REGISTRY_ID` など）は、設定するたびに `$HOME/issue2-vars.sh` にも追記します。追記する値は ID と ARN だけで、機密情報は含みません。CloudShell はしばらく操作しないとセッションが切れます。切れた場合は、次のコマンドで変数を戻してから続きを実行します。

```bash
set -o pipefail
export AWS_REGION=us-east-1
source "$HOME/issue2-vars.sh"
```

作業ファイル（`$HOME/registry.json` など）は CloudShell のホームディレクトリに残るので、作り直す必要はありません。手順 9 の最後にまとめて削除します。

## 1. Registry を特定する

AWS Agent Registry の現在の名前空間は `agent-registry` です。パブリックプレビューの `bedrock-agentcore` 名前空間のサポートは 2026 年 10 月 30 日に終了しています。`agent-registry-control` で一覧に出る Registry が、`agent-registry` 名前空間の Registry です。

### コンソール

1. コンソール上部の検索欄に `Agent Registry` と入力し、**AWS エージェントレジストリ**（AWS Agent Registry）のコンソールを開きます。
2. ナビゲーションペインの **検出**（Discover）で **レジストリ**（Registries）を選びます。
3. 一覧の **名前**、**認証タイプ**、**ステータス**、**ARN** を確認します。

### CloudShell

```bash
aws agent-registry-control list-registries --output table \
  --query 'registries[].[name,registryId,status,discoveryConfiguration.authorizerType,registryArn]'
```

結果に応じて次のように進めます。

- **対象が 1 つに決まる:** 次へ進みます。
- **複数ある:** 用途や名前から対象を決め、選んだ理由を記録します。
- **1 つもない:** 既存 Registry がないことを前提との差分として記録し、手順 9 で親 Issue に報告します。手順 2〜7 は行いません。Registry の作成は Issue #2 の範囲外です。

対象の ID を変数に設定します。

```bash
REGISTRY_ID='一覧で確認した registryId'
echo "REGISTRY_ID='$REGISTRY_ID'" >> "$HOME/issue2-vars.sh"
```

## 2. Registry の詳細と自動承認を確認する

### コンソール

一覧で対象の名前を選びます。**レジストリの詳細**（Registry details）セクションで、名前、ステータス、**自動承認**（Auto approval）、レジストリ ARN を確認します。

### CloudShell

```bash
aws agent-registry-control get-registry --registry-id "$REGISTRY_ID" --output json \
  > "$HOME/registry.json"

jq '{name, registryId, registryArn, status,
     authorizerType: .discoveryConfiguration.authorizerType,
     autoApprovalRules: (.approvalConfiguration.autoApprovalRules // []),
     autoDetection: .autoDetection.status,
     kmsKeyArn: .encryptionConfiguration.kmsKeyArn}' "$HOME/registry.json"
```

次を確認して記録します。

- `registryArn` が `arn:aws:agent-registry:us-east-1:<アカウント ID>:registry/<registryId>` の形であること。これで `agent-registry` 名前空間と us-east-1 を確認できます。
- `status` が `READY` であること。
- `autoApprovalRules` が `[]` であること。空なら、申請は手動の審査が必要です（自動承認は無効）。`APPROVE_ALL` が含まれていれば自動承認が有効です。差分として記録し、手順 9 で報告します。設定は変更しません。

[レジストリの作成と管理](https://docs.aws.amazon.com/ja_jp/bedrock-agentcore/latest/devguide/registry-create-manage.html)

## 3. custom metadata schema を確認する

custom metadata schema には、全レコード種別に共通の属性（API の `defaultSchema`）と、種別ごとの追加フィールド（`recordTypeSchemaOverrides`）があります。どちらも JSON Schema です。

### コンソール

レジストリの詳細ページの **カスタムメタデータスキーマ**（Custom metadata schema）セクションで確認します。**スキーマの管理**（Manage schema）は編集画面です。開いた場合は保存せずに閉じます。

### CloudShell

```bash
jq '.customMetadataSchemaConfiguration as $c
    | {
        common: ($c.defaultSchema // null | if . == null then null else fromjson end),
        agent: ([$c.recordTypeSchemaOverrides[]? | select(.recordType == "AGENT") | .schema | fromjson] | first)
      }' "$HOME/registry.json"
```

次を記録します。`null` はそのスキーマが未設定であることを表します。

- `AGENT` が使えるフィールド。`common.properties` と `agent.properties` の両方が対象です。フィールドごとに名前、型、必須かどうか（`required` に含まれるか）を記録します。
- `isA2A` の有無。ある場合は、定義されている場所（`common` か `agent` か）、型、必須かどうかを記録します。

設計上の `isA2A` は、`AGENT` 専用の必須の Boolean です。現状と違えば差分として記録します。スキーマの変更は `UpdateRegistry` が必要で、`RegistryPortalDeployer` では拒否されるため、変更の方法は親 Issue で決めます。[ADR-0006](../adr/0006-a2a-search-metadata-as-copy.md) · [Custom metadata schema](https://docs.aws.amazon.com/ja_jp/bedrock-agentcore/latest/devguide/registry-custom-metadata-schema.html)

## 4. 発見用 M2M の Cognito 設定と secret の保管先を確認する

### Registry の受信認可

```bash
jq '.discoveryConfiguration | {authorizerType,
     jwt: (.authorizerConfiguration.customJWTAuthorizer // null
           | if . == null then null else {discoveryUrl, allowedClients, allowedAudience, allowedScopes,
               customClaims: ((.customClaims // []) | length)} end)}' "$HOME/registry.json"
```

- `authorizerType` が `CUSTOM_JWT` であることを確認します。`AWS_IAM` なら発見用 API は IAM で認可されるため、Cognito の M2M は使われていません。差分として記録し、手順 4 の残りと手順 6 は行いません。認可タイプは Registry の作成後に変更できません。
- `discoveryUrl` が `https://cognito-idp.<リージョン>.amazonaws.com/<User Pool ID>/.well-known/openid-configuration` の形なら、IdP は Cognito User Pool です。URL から User Pool のリージョンと ID を読み取ります。Cognito 以外の URL なら差分として記録します。
- `allowedClients`、`allowedAudience`、`allowedScopes` と、カスタムクレームのルール数を記録します。設定されたルールはすべて検証されます。`allowedScopes` がなければ「Registry 側の scope 制限なし」と記録します。

```bash
COGNITO_REGION='discoveryUrl のリージョン'
USER_POOL_ID='discoveryUrl の User Pool ID'
echo "COGNITO_REGION='$COGNITO_REGION'" >> "$HOME/issue2-vars.sh"
echo "USER_POOL_ID='$USER_POOL_ID'" >> "$HOME/issue2-vars.sh"
```

### 発見用の app client

1. コンソールで **Amazon Cognito** → **ユーザープール**（User pools）→ 対象のユーザープールを開きます。
2. **アプリケーション**（Applications）→ **アプリケーションクライアント**（App clients）で、発見用の M2M クライアントを探します。`allowedClients` が設定されていれば、その client_id のクライアントです。
3. CloudShell で設定を確認します。`--query` で、クライアントシークレットを出力に含めないようにしています。`--query` を外したり `--debug` を付けたりしないでください。

```bash
DISCOVERY_CLIENT_ID='発見用の client_id'
echo "DISCOVERY_CLIENT_ID='$DISCOVERY_CLIENT_ID'" >> "$HOME/issue2-vars.sh"

aws cognito-idp describe-user-pool-client --region "$COGNITO_REGION" \
  --user-pool-id "$USER_POOL_ID" --client-id "$DISCOVERY_CLIENT_ID" --output json \
  --query 'UserPoolClient.{name:ClientName,id:ClientId,oauthEnabled:AllowedOAuthFlowsUserPoolClient,flows:AllowedOAuthFlows,scopes:AllowedOAuthScopes,hasSecret:(ClientSecret!=`null`)}'

aws cognito-idp describe-user-pool --region "$COGNITO_REGION" --user-pool-id "$USER_POOL_ID" \
  --query 'UserPool.{domain:Domain,customDomain:CustomDomain}' --output json
```

次を確認して記録します。

- `oauthEnabled` が `true`、`flows` に `client_credentials` があり、`hasSecret` が `true` であること。M2M として使えるクライアントの条件です。
- `scopes`（Cognito が発行できる scope）と、Registry の `allowedScopes` が少なくとも 1 つ一致すること。
- `domain` または `customDomain` があること。トークンの発行に Cognito ドメインが必要です。
- `COGNITO_REGION` が `us-east-1` であること。ポータルの本体スタック（Issue #5）は、JWT の issuer を `https://cognito-idp.us-east-1.amazonaws.com/<User Pool ID>` に固定しています。別のリージョンなら差分として記録します。

発見用のクライアントが見つからない、または条件を満たさない場合は差分として記録し、手順 6 は行いません。コンソールのクイック作成で作られた Registry では、M2M のクライアントがないことがあります。

### secret の保管先

設計では、発見用 M2M のクライアントシークレットを Secrets Manager に保管し、管理 Lambda がその ARN を参照します。

1. コンソールで **Secrets Manager** → **シークレット**（Secrets）を開き、名前や説明から発見用クライアントのシークレットを探します。
2. CloudShell でメタデータを確認します。このロールでは secret の値は読めません。

```bash
aws secretsmanager list-secrets --region "$COGNITO_REGION" --output table \
  --query 'SecretList[].[Name,ARN,Description]'

SECRET_ARN='確認した secret の ARN'
echo "SECRET_ARN='$SECRET_ARN'" >> "$HOME/issue2-vars.sh"
aws secretsmanager describe-secret --region "$COGNITO_REGION" --secret-id "$SECRET_ARN" \
  --query '{arn:ARN,name:Name,description:Description,kmsKeyId:KmsKeyId,deletedDate:DeletedDate,tags:Tags}' --output json
```

`deletedDate` がないこと（削除予定でないこと）を確認し、ARN と `kmsKeyId` を記録します。`kmsKeyId` がなければ、secret は AWS 管理キー（`aws/secretsmanager`）で暗号化されています。`kmsKeyId` の扱いは手順 7 で決めます。値を読めないため、secret と client_id の対応は、名前、説明、タグで判断します。判断できない場合や、secret が Secrets Manager にない場合は差分として記録します。secret が別のリージョンにある場合は `--region` を変えて探します。

[サポートされている受信認可タイプ](https://docs.aws.amazon.com/ja_jp/bedrock-agentcore/latest/devguide/registry-supported-auth-types.html)

## 5. レコードの件数と作成経路を調べる

### 全レコードを数える

管理 API の `ListRegistryRecords` は、全種別・全状態のレコードを最新リビジョンで返します。AWS CLI が自動でページをたどるため、`--max-items` や `--filters` は付けません。

```bash
aws agent-registry-control list-registry-records --registry-id "$REGISTRY_ID" --output json \
  > "$HOME/records.json"
date -u +%Y-%m-%dT%H:%M:%SZ

jq '.registryRecords | {
      total: length,
      uniqueIds: (map(.recordId) | unique | length),
      byTypeAndStatus: (group_by([.recordType, .status])
        | map({type: .[0].recordType, status: .[0].status, count: length})),
      autoDetected: (map(select(.createdByAutoDetection == true)) | length),
      schemaNonCompliant: (map(select(.customMetadataSchemaComplianceStatus == "NON_COMPLIANT")) | length)
    }' "$HOME/records.json"
```

`total` と `uniqueIds` が一致することを確認します。一致しない場合は、少し時間をおいて取り直します。取得日時と件数を記録します。

レコードごとの一覧は次のコマンドで表示します。

```bash
jq -r '.registryRecords[] | [.recordId, .recordType, .status, .createdAt, (.createdBy // "-"),
       (.createdByAutoDetection // false), ((.provenanceSummaryList // []) | map(.sourceType) | join(","))]
       | @tsv' "$HOME/records.json"
```

コンソールでは、レジストリの詳細ページの **レジストリレコード**（Registry records）セクションで、状態ごとの件数とレコードの一覧を確認できます。

### 作成経路を調べる

1. `createdByAutoDetection` が `true` のレコードは「AWS の自動検出」です。`provenanceSummaryList` の `sourceType` に検出元（AgentCore Runtime や Gateway）が入ります。
2. それ以外のレコードは CloudTrail の `CreateRegistryRecord` イベントで調べます。CloudTrail の **イベント履歴**（Event history）で見られるのは直近 90 日の管理イベントです。

   コンソールでは **CloudTrail** → **イベント履歴** を開き、**ルックアップ属性**（Lookup attributes）で **イベント名**（Event name）を選んで `CreateRegistryRecord` を入力します。CloudShell では次のように一覧にします。

   ```bash
   aws cloudtrail lookup-events --region us-east-1 --output json \
     --lookup-attributes AttributeKey=EventName,AttributeValue=CreateRegistryRecord \
     | jq -r '.Events[].CloudTrailEvent | fromjson
         | [.eventTime, .eventID, (.userIdentity.arn // .userIdentity.type), .userAgent,
            (.requestParameters.registryId // "-"),
            (.responseElements.recordId // .responseElements.recordArn // "-"),
            (.errorCode // "成功")] | @tsv'
   ```

3. イベントとレコードを、レコード ID（取れない場合は Registry ID と作成日時）で対応付けます。`userAgent` から作成経路を判断します。

   | `userAgent` の例 | 作成経路 |
   | --- | --- |
   | `aws-cli/2...` | AWS CLI |
   | `Boto3/...`、`aws-sdk-...` | SDK を使うスクリプトやアプリ |
   | `...Terraform/...`、`...HashiCorp...` | Terraform（IaC） |
   | `AWS Internal`、コンソールの UA | コンソール |

4. 90 日より前に作られたレコードは、イベント履歴では調べられません。証跡（trail）を S3 に保存していればそのログを、なければ当時の作業記録を確認します。根拠がなければ「不明」とし、参照した期間と資料を記録します。個人アカウントには組織の証跡がないので、証跡があるかを次のコマンドで確認します。結果が空なら、証跡はありません。

   ```bash
   aws cloudtrail describe-trails --region us-east-1 \
     --query 'trailList[].[Name,S3BucketName,IsMultiRegionTrail,HomeRegion]' --output table
   ```
5. 経路ごとの件数を合計し、`total` と一致することを確認します。

[Registry の CloudTrail ログ](https://docs.aws.amazon.com/ja_jp/bedrock-agentcore/latest/devguide/registry-cloudtrail-integration.html) · [CloudTrail のイベント履歴](https://docs.aws.amazon.com/ja_jp/awscloudtrail/latest/userguide/view-cloudtrail-events.html)

## 6. 公開中の AGENT を Agent Card の有無で数える

発見用 API（`ListDiscoverableRegistryRecords`）は、承認済み（`APPROVED`）のリビジョンだけを返します。公開中のレコードを編集して下書きがある場合も、公開中のリビジョンが返ります。管理 API の件数とは一致しないことがあります。

JWT で認可される Registry の発見用 API は、IAM ではなく Bearer トークンで呼びます。次のスクリプトは、発見用クライアントでトークンを取得し、`AGENT` の公開レコードを全ページ取得して、件数だけを表示します。クライアントシークレットとトークンは関数の中だけで使い、表示もファイル保存もしません。

手順 4 で設定した `COGNITO_REGION`、`USER_POOL_ID`、`DISCOVERY_CLIENT_ID` を使います。カスタムドメインを使っている場合は、`TOKEN_URL` をそのドメインの `/oauth2/token` に置き換えます。

```bash
COGNITO_DOMAIN=$(aws cognito-idp describe-user-pool --region "$COGNITO_REGION" \
  --user-pool-id "$USER_POOL_ID" --query 'UserPool.Domain' --output text)
TOKEN_URL="https://${COGNITO_DOMAIN}.auth.${COGNITO_REGION}.amazoncognito.com/oauth2/token"

count_published_agents() (
  set -euo pipefail
  local secret token next body page
  secret=$(aws cognito-idp describe-user-pool-client --region "$COGNITO_REGION" \
    --user-pool-id "$USER_POOL_ID" --client-id "$DISCOVERY_CLIENT_ID" \
    --query 'UserPoolClient.ClientSecret' --output text)
  token=$(curl -sS --fail -u "${DISCOVERY_CLIENT_ID}:${secret}" \
    -d grant_type=client_credentials "$TOKEN_URL" | jq -er '.access_token')
  unset secret
  : > "$HOME/published-agents.jsonl"
  next=""
  while :; do
    body=$(jq -nc --arg t "$next" \
      '{maxResults: 100, filters: [{name: "recordType", values: ["AGENT"]}]}
       + (if $t == "" then {} else {nextToken: $t} end)')
    page=$(curl -sS --fail -X POST \
      "https://agent-registry.us-east-1.api.aws/registries/${REGISTRY_ID}/discoverable-records-list" \
      -H "Authorization: Bearer ${token}" -H "Content-Type: application/json" -d "$body")
    jq -c '.registryRecords[] | {recordId, recordType, recordVersion, status, descriptorTypes}' \
      <<< "$page" >> "$HOME/published-agents.jsonl"
    next=$(jq -r '.nextToken // ""' <<< "$page")
    [ -z "$next" ] && break
  done
  jq -s '{
      "公開中のAGENT": length,
      "ID重複": (length - (map(.recordId) | unique | length)),
      "Agent Cardあり": (map(select((.descriptorTypes // []) | index("a2aAgentCard"))) | length),
      "Agent Cardなし": (map(select(((.descriptorTypes // []) | index("a2aAgentCard")) | not)) | length)
    }' "$HOME/published-agents.jsonl"
)

date -u +%Y-%m-%dT%H:%M:%SZ
count_published_agents
```

- 取得日時と 3 つの件数を記録します。`ID重複` が `0` でなければ、時間をおいて取り直します。
- 判定には、一覧の各レコードにある `descriptorTypes`（レコードが持つ descriptor の種類）を使います。`a2aAgentCard` を含むものを「Agent Card あり」とします。
- `curl: (22) ... 400` などでトークンの取得に失敗した場合は、手順 4 のクライアント設定（`client_credentials`、secret の有無、ドメイン）を見直します。発見用 API が 401 や 403 を返す場合は、Registry の `allowedClients`、`allowedAudience`、`allowedScopes`、カスタムクレームと、トークンの内容が合っているかを見直します。
- `$HOME/published-agents.jsonl` には、公開中レコードの ID と種別だけが入ります。手順 9 の最後に削除します。

[承認済みレコードの参照](https://docs.aws.amazon.com/ja_jp/bedrock-agentcore/latest/devguide/registry-browse-records.html) · [レコードのライフサイクル](https://docs.aws.amazon.com/ja_jp/bedrock-agentcore/latest/devguide/registry-record-lifecycle.html) · [Cognito のトークンエンドポイント](https://docs.aws.amazon.com/ja_jp/cognito/latest/developerguide/token-endpoint.html)

## 7. Terraform に渡す値をまとめ、名前の重なりを確認する

ポータルを配信するときに `dev.tfvars` に設定する値を、ここまでの結果からまとめます（[Terraform ガイドの 3.2](registry-portal-terraform-guide-ja.md#32-事前に確定する値)）。あわせて、ポータルが作る Cognito の資源と IAM ロールの名前が、既存のものと重ならないかを確認します。ここでも AWS は変更しません。手順 4 で設定した `COGNITO_REGION` と `USER_POOL_ID` を使います。

手順 4 で認可タイプが `AWS_IAM` だった場合や、発見用のクライアントや secret が見つからなかった場合は、`discovery_` で始まる値を決められません。記録には「未確定（手順 4 の差分による）」と書き、手順 9 で差分として報告します。人間用の User Pool が決まっていれば、この手順のほかの確認は行います。

### Cognito ドメインとトークン URL

```bash
aws cognito-idp describe-user-pool --region "$COGNITO_REGION" --user-pool-id "$USER_POOL_ID" \
  --query 'UserPool.{domain:Domain,customDomain:CustomDomain,adminCreateOnly:AdminCreateUserConfig.AllowAdminCreateUserOnly}' \
  --output json
```

- `cognito_domain`: `customDomain` があれば `https://<customDomain>`、なければ `https://<domain>.auth.<COGNITO_REGION>.amazoncognito.com` です。末尾に `/` は付けません。人間用のログイン画面と発見用 M2M のトークン発行は、同じ User Pool のこのドメインを使います。
- `discovery_token_url`: `cognito_domain` の後ろに `/oauth2/token` を付けた値です。手順 6 の `TOKEN_URL` と同じになります。
- `adminCreateOnly` が `true` であることを確認します。設計では、セルフサインアップを無効にし、ユーザーは Admin が作成します。`false` なら差分として記録します。User Pool の本体は Terraform の管理対象ではないため、変更する場合はポータルの配信とは別に行います。

### 人間用の IdP 名

```bash
aws cognito-idp list-identity-providers --region "$COGNITO_REGION" --user-pool-id "$USER_POOL_ID" \
  --query 'Providers[].[ProviderName,ProviderType]' --output table
```

- `identity_providers`: User Pool のユーザー（ユーザー名とパスワード）でログインする場合は `["COGNITO"]` です。外部 IdP がない個人アカウントでは、通常これになります。
- 外部の IdP（SAML、OIDC、ソーシャルログイン）でログインさせる場合は、その `ProviderName` を指定します。

### 発見用 M2M の scope と secret の KMS キー

- `discovery_scope`: 手順 4 で確認した、client の `scopes` と Registry の `allowedScopes` の両方に含まれる scope を 1 つ指定します。Registry に `allowedScopes` がなければ、空文字（client の既定の scope）にできます。
- `discovery_kms_key_arn`: 手順 4 の `kmsKeyId` の形で決めます。`RegistryPortalReadOnly` には KMS の権限がないため、エイリアスの場合だけ別のロールを使います。
  - `kmsKeyId` がない: `null` です。
  - `arn:aws:kms:...:key/...` の形: そのまま指定します。
  - キー ID（`1234abcd-...` の形）: secret と同じリージョン・アカウントのキーなので、`arn:aws:kms:<secret のリージョン>:<アカウント ID>:key/<キー ID>` と組み立てます。
  - エイリアス（`alias/...` を含む形）: エイリアスはキーの ARN ではないため、指定できません。この確認手順で唯一、ローカル端末の `registry-deploy` プロファイル（[IAM 手順の 9](registry-portal-iam-setup-personal-account-ja.md#ローカル端末を使う)）を使い、`aws kms describe-key --key-id '<kmsKeyId の値>' --region '<secret のリージョン>' --profile registry-deploy --query KeyMetadata.Arn --output text` で ARN を調べます。読み取りだけで、AWS は変更しません。どのロールで調べたかを記録します。

### 名前の重なり

ポータルの Terraform は、既存の User Pool にグループ・リソースサーバー・人間用 client を追加し、IAM ロールも作ります。同じ名前のものが既にあると、apply が失敗したり、同じ資源を 2 つの場所で管理することになったりします。

```bash
# Cognito のグループ（registry-publisher、registry-curator、registry-admin、team-*）
aws cognito-idp list-groups --region "$COGNITO_REGION" --user-pool-id "$USER_POOL_ID" \
  --query "Groups[?starts_with(GroupName, 'registry-') || starts_with(GroupName, 'team-')].GroupName" \
  --output text

# リソースサーバー（識別子 registry-portal）
aws cognito-idp list-resource-servers --region "$COGNITO_REGION" --user-pool-id "$USER_POOL_ID" \
  --max-results 50 --query "ResourceServers[?Identifier=='registry-portal'].[Identifier,Name]" --output text

# 人間用 client（名前 registry-portal-dev-human）
aws cognito-idp list-user-pool-clients --region "$COGNITO_REGION" --user-pool-id "$USER_POOL_ID" \
  --query "UserPoolClients[?starts_with(ClientName, 'registry-portal')].[ClientName,ClientId]" --output text

# Lambda の実行ロール（registry-portal-dev-management、registry-portal-decision、registry-portal-events）
aws iam list-roles --query "Roles[?starts_with(RoleName, 'registry-portal')].RoleName" --output text
```

- 何も表示されなければ、名前は重なっていません。
- [IAM の作成手順](registry-portal-iam-setup-personal-account-ja.md)で作ったロール（`RegistryPortalReadOnly` など）は、名前が大文字で始まるため、最後のコマンドには表示されません。
- グループやリソースサーバーが既にある場合は、作った人と用途を確認して記録します。ポータルで使うなら、配信のときに Terraform に取り込みます（[Terraform ガイドの 2.2](registry-portal-terraform-guide-ja.md#22-同じ名前の資源がすでにある場合)）。
- 同じ名前の IAM ロールや client がある場合は、ポータル以外の用途でないかを確認し、`name_prefix` を変えるかどうかを記録します。

### 所属チーム名を決める

`team_names` には、Publisher が所属するチームの名前を指定します（例: `payments`。`team-` は付けません）。所有チームは Registry レコードの管理情報に保存されるため、後から名前を変えるには移行が必要です。dev で使うチームを決めて記録します。

### まとめる値

| 変数 | 値の出どころ |
| --- | --- |
| `aws_account_id` | `aws sts get-caller-identity` の `Account`（手順 0） |
| `registry_arn` | 手順 2 の `registryArn` |
| `cognito_user_pool_id` | 手順 4 の `USER_POOL_ID` |
| `cognito_domain` | この手順の「Cognito ドメインとトークン URL」 |
| `identity_providers` | この手順の「人間用の IdP 名」 |
| `team_names` | この手順の「所属チーム名を決める」 |
| `discovery_token_url` | この手順の「Cognito ドメインとトークン URL」 |
| `discovery_client_id` | 手順 4 の `DISCOVERY_CLIENT_ID` |
| `discovery_client_secret_arn` | 手順 4 の `SECRET_ARN`（完全な ARN） |
| `discovery_kms_key_arn` | この手順の「発見用 M2M の scope と secret の KMS キー」 |
| `discovery_scope` | 同上 |

これらの値は機密情報ではありませんが、secret の値やトークンは含めないでください。

## 8. break-glass の担当者と利用手順を決める

### 担当者とロール

個人アカウントでは、次のように記録します。

| 項目 | 記録する内容 |
| --- | --- |
| 管理ロール | `RegistryPortalBreakGlass` の ARN |
| 利用者 | 本人（`registry-operator`） |
| 承認者・監査担当者 | 本人。担当者を分けられないことを差分として記録します。 |
| 利用手順の保管先 | この手順書と、利用ごとに起票する Issue |

### 利用手順

break-glass は、ポータルを経由できない緊急時だけ使います。通常の変更はポータルで行います。[ADR-0001](../adr/0001-registry-writes-only-through-portal.md)

1. **起票する。** 利用前に Issue を作り、理由、対象の Registry ID とレコード ID、予定する操作、作業時間、中止する条件を書きます。
2. **ロールを切り替える。** コンソールで `RegistryPortalBreakGlass` に切り替えるか、ローカル端末で `registry-break-glass` プロファイルを使います。`aws sts get-caller-identity --profile registry-break-glass` の `Arn` が `assumed-role/RegistryPortalBreakGlass/` を含むことを確認します。
3. **変更前の状態を保存する。** `aws agent-registry-control get-registry-record --registry-id <ID> --record-id <ID> --profile registry-break-glass` の結果を作業記録に残します（secret などの機密値が含まれていないか確認してから残します）。
4. **変更する。** 起票した操作だけを行います。`update-registry-record` の `--custom-metadata` は、送った内容でメタデータ全体が置き換わることを前提に、既存のキーをすべて含めて送ります。
5. **変更後を確認する。** もう一度 `get-registry-record` を実行し、意図した値になったこと、ほかの項目が変わっていないことを確認します。
6. **監査の記録を残す。** CloudTrail のイベント履歴で、イベントソース `agent-registry.amazonaws.com` の該当イベントを探し、イベント ID、操作主体、時刻を Issue に追記します。ポータルをデプロイ済みなら、Admin 画面の「ポータル外の変更」の通知も確認します。
7. **終了する。** **スイッチバック** で元のユーザーに戻り、Issue を閉じます。

### ポータル外で `isA2A` を補完する場合

通常は、[Issue #16](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/16) の取り込みのあと、所有チームの編集・申請・公開承認で補完します。break-glass で補完するときは、次の制約があります。

- 公開中（`APPROVED`）のレコードを編集すると、新しい下書き（`DRAFT`）のリビジョンができます。公開中のリビジョンは変わらず、発見用 API も古いメタデータを返し続けます。
- 公開に反映するには、申請（`SubmitRegistryRecordForApproval`）と承認（`UpdateRegistryRecordStatus`）が必要です。`RegistryPortalBreakGlass` には、この 2 つの権限を付けていません。
- 個人アカウントでは、申請と承認を同じ人が行うことになり、自己承認の禁止（ADR-0001）を満たせません。

そのため、`isA2A` の補完は原則として通常の手順で行います。どうしても break-glass で行う場合は、次のようにします。

1. 起票した Issue に、対象のレコード ID、公開中の版、`a2aAgentCard` の有無から決めた `isA2A` の値、自己承認になることを書きます。
2. ルートユーザーで `RegistryPortalBreakGlass` のインラインポリシーに、対象レコードの ARN に限った `agent-registry:SubmitRegistryRecordForApproval` と `agent-registry:UpdateRegistryRecordStatus` を一時的に追加します。
3. 上の利用手順で更新・申請・承認を行い、発見用 API で公開中のリビジョンに反映されたことを確認します。
4. 一時的に追加した権限を削除し、削除したことを Issue に記録します。

## 9. 記録して前提との差分を報告する

[Issue #2 の作業記録テンプレート](registry-portal-issue2-record-template-ja.md#2-記録テンプレート)を使って Issue #2 にコメントし、Issue #2 の該当項目にチェックを付けます。secret の値、トークン、非公開のレコード本体は Issue に貼り付けません。このリポジトリは公開されているため、ARN などに含まれる AWS アカウント ID は末尾 4 桁だけを残して伏せます（[値の扱い](registry-portal-issue2-record-template-ja.md#値の扱い)）。

記録には次の内容が含まれます。



- [x] Registry の名前、ID、ARN、リージョン、名前空間、状態、確認日時（手順 1・2）
- [x] 自動承認の判定（`autoApprovalRules` の値）（手順 2）
- [ ] `AGENT` が使えるフィールドと、`isA2A` の有無・定義場所・型・必須かどうか（手順 3）
- [ ] 認可タイプ、User Pool ID、発見用 client_id、secret の保管先 ARN、Cognito と Registry それぞれの scope（手順 4）
- [ ] 全件数、種別・状態ごとの件数、作成経路ごとの件数と根拠、不明の件数（手順 5）
- [ ] 公開中の `AGENT` の件数と、Agent Card あり・なしの件数、取得日時（手順 6）
- [ ] Terraform に渡す値の一覧、セルフサインアップの設定、名前の重なりの有無（手順 7）
- [ ] break-glass のロール ARN、担当者、利用手順の保管先、`isA2A` 補完時の制約（手順 8）

前提と違う点があれば、[親 Issue #1](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/1) に項目ごとに次の形でコメントします。permission set を IAM ロールで代替した件（[IAM の作成手順の手順 10](registry-portal-iam-setup-personal-account-ja.md#10-issue-2-に記録する)）と、IAM Identity Center がないため Issue #5 の bootstrap と `deploy.sh` をそのまま使えない件（[Terraform ガイドの 7](registry-portal-terraform-guide-ja.md#7-既知の食い違いと未解決事項)）も、同じコメントにまとめます。

```markdown
### <項目名>
- **想定:** <設計書や Issue の前提>
- **確認結果:** <確認した事実と確認日時>
- **後続作業への影響:** <影響する Issue や設計>
- **判断が必要なこと:** <決めてほしいこと>
```

この手順で差分になりやすい点は次のとおりです。

- 担当者を分けられない（break-glass の承認者・監査担当者、`isA2A` 補完時の自己承認）。
- `isA2A` がスキーマにない、または `AGENT` 専用の必須 Boolean になっていない。
- 発見用 M2M のクライアントがない、または secret が Secrets Manager にない。
- IAM Identity Center がなく、permission set を使えない（IAM ロールで代替する）。
- User Pool が us-east-1 にない、またはセルフサインアップが有効になっている。
- ポータルが作るグループ・リソースサーバー・IAM ロールと同じ名前のものが既にある。
- 組織の証跡がなく、90 日より前のレコードの作成経路を調べられない。

### 後片付け

記録を投稿したら、CloudShell の作業ファイルを削除します。どれも機密情報は含みませんが、残す必要はありません。CloudShell のホームディレクトリは、使わないまま 120 日たつと削除されます。

```bash
rm -f "$HOME/registry.json" "$HOME/records.json" "$HOME/published-agents.jsonl" "$HOME/issue2-vars.sh"
```

最後に、右上のメニューから **スイッチバック**（Switch back）を選び、元のユーザーに戻ります。
