# Registry ポータルの AWS 接続と運用権限を準備する

[Issue #2](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/2) の担当者が、既存の AWS Agent Registry を確認し、デプロイ権限と緊急時の運用を準備するための手順書です。[PR #20 からの引き継ぎ](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/2#issuecomment-6661101101)も含みます。AWS の操作は人間の担当者が行います。

## 1. 作業に必要な担当者とアクセスを確認する

1. Registry の所有アカウント、対象 Registry の名前または ID、作業日時、証跡の保管先を確認します。アカウントと Registry の候補は、既存の構成台帳または Registry の管理者に確認します。
2. AWS を確認する担当者には、IAM Identity Center で割り当て済みの権限を使います。Registry の一覧・詳細、Cognito の設定、secret のメタデータ、CloudTrail の履歴を読めることを確認します。Cognito や secret が別アカウントにある場合は、そのアカウントの担当者が確認します。
3. `RegistryPortalDeployer` を作成する IAM Identity Center 管理者、デプロイ担当者、break-glass の運用責任者を確認します。発見用 M2M の認証情報は、既存の管理担当者が扱います。
4. 人間の作業端末で、`agent-registry-control` に対応した AWS CLI v2、Bash、`jq`、Python 3 を用意します。以降のシェル例は同じ Bash セッションで実行します。コマンドが失敗したら、エラーを確認してから次の手順へ進みます。

### 必要なユーザーとロール

通常の構成で、新規に作成する IAM ユーザーは **0 人**です。AWS を操作する担当者は、組織の IAM Identity Center ユーザーでサインインします。既存のユーザーとグループに担当を割り当てます。

| 用途 | 必要なユーザー・権限・ロール | Issue #2 で行うこと |
| --- | --- | --- |
| AWS 環境の確認 | Registry、Cognito、secret のメタデータ、CloudTrail を確認できる担当者と読み取り権限。 | 既存の SSO 権限を確認します。対象が別アカウントにある場合は、そのアカウントの権限を持つ担当者が確認します。 |
| デプロイ | IAM Identity Center の管理者、デプロイ担当者、専用の permission set `RegistryPortalDeployer`。 | permission set を用意して対象アカウントのデプロイ担当者に割り当てます。既存なら割り当てと明示的な拒否を検証します。対応する `AWSReservedSSO_RegistryPortalDeployer_…` ロールは IAM Identity Center が作成・管理します。 |
| 緊急対応 | break-glass 用の IAM ロール、利用担当者、承認・監査の担当者。 | `RegistryPortalDeployer` と別のロールを指定します。対象 Registry への必要な権限と監査手順を備えた既存ロールを使えます。該当するロールがなければ新設します。 |
| ポータルの実行 | 管理 Lambda、決裁 Lambda、イベント処理 Lambda のそれぞれに対応する実行ロール。 | 3 つのロールはポータルのデプロイで用意します。Issue #2 の手作業では作成しません。 |

[IAM Identity Center の permission set と IAM ロール](https://docs.aws.amazon.com/singlesignon/latest/userguide/permissionsetsconcept.html) · [Registry ポータル MVP1 設計書](../../plan/aws-agent-registry-mvp1-design-ja.md) · [ADR-0001](../adr/0001-registry-writes-only-through-portal.md)

### 作成が不要なユーザーとロール

- **確認・デプロイ・通常運用のための IAM ユーザーと長期アクセスキー。** 既存の IAM Identity Center ユーザーを使います。IAM Identity Center の障害時にも緊急対応するなど、組織が IAM ユーザーを必要と判断した場合に限り、後述の作成手順を使います。
- **`RegistryPortalReadOnly` などの専用読み取り IAM ロール。** 必要な読み取り権限が既存の SSO 割り当てにあれば、追加は不要です。同じアカウントと権限で確認できるサービスには、同じ AWS CLI プロファイルを使えます。
- **デプロイ用 IAM ロールの手動作成。** `AWSReservedSSO_RegistryPortalDeployer_…` は permission set の割り当てで用意します。
- **ポータル利用者や M2M クライアント用の IAM ユーザー・ロール。** Viewer、Publisher、Curator、Admin の認証とアプリ内の権限には Cognito を使います。発見用 M2M と CI のポータル API 用の認証にも Cognito クライアントを使います。
- **GitHub Actions から AWS へデプロイする IAM ロール。** AWS への反映はデプロイ担当者がローカル端末から行います。[ADR-0004](../adr/0004-no-aws-deployment-from-ci.md)

### AWS CLI プロファイルに使う情報をそろえる

AWS CLI のプロファイルは、接続する AWS アカウントと、そのアカウントで使う権限を名前で選ぶためのローカル設定です。各担当者が自分の作業端末で作成します。事前に次の情報をそろえます。

| 確認する値 | 確認方法 |
| --- | --- |
| SSO 開始 URL | 組織から案内された AWS アクセスポータルの URL を使います。IAM Identity Center 管理者は、インスタンスの **Settings** で確認できます。 |
| SSO リージョン | IAM Identity Center インスタンスがあるリージョンを管理者に確認します。接続先サービスのリージョンは別に確認します。 |
| AWS アカウント ID | アクセスポータルの **AWS accounts** で、台帳と照合した 12 桁の ID を控えます。 |
| 権限名 | アクセスポータルで対象アカウントを開き、自分に割り当てられた権限の表示名を確認します。`RegistryPortalDeployer` は手順 6 で割り当てが済んでから選べます。 |

まず Registry の読み取り用プロファイルを作成します。次の表の名前は作業端末で付ける例です。既存プロファイルを使う場合は、実際の名前を変数に設定し、下記の `get-caller-identity` で接続先を照合します。

| 手順書で使う変数 | プロファイル名の例 | 選ぶアカウントと権限 |
| --- | --- | --- |
| `READ_PROFILE` | `registry-read` | Registry の所有アカウントで、Registry と CloudTrail を読み取れる権限。 |
| `COGNITO_PROFILE` | `registry-cognito-read` | User Pool の所有アカウントで、Cognito の設定を読み取れる権限。 |
| `SECRET_PROFILE` | `registry-secret-metadata` | secret の所有アカウントで、Secrets Manager のメタデータを読み取れる権限。 |
| `IAM_ADMIN_PROFILE` | `registry-iam-admin` | 対象の IAM ロールがあるアカウントで、IAM Policy Simulator を CLI から実行できる権限。管理者が使います。 |
| `DEPLOY_PROFILE` | `registry-deploy` | デプロイ先アカウントの `RegistryPortalDeployer`。手順 6 で割り当てた後に作成します。 |

同じアカウントと権限で手順 3 の確認までできる場合は、`COGNITO_PROFILE` または `SECRET_PROFILE` に `READ_PROFILE` と同じ名前を指定できます。アカウントまたは権限が異なる場合は、`aws configure sso --profile registry-cognito-read` のように、用途ごとの名前を指定して作成手順を繰り返します。同じ IAM Identity Center インスタンスと同じ利用者なら、SSO セッション名をプロファイル間で共有できます。

### 読み取り用プロファイルを作成してログインする

1. AWS CLI のバージョンと既存プロファイル名を確認します。新規に作る場合は、既存の名前と重ならない `READ_PROFILE` を選びます。

```bash
set -o pipefail
REGION=us-east-1
READ_PROFILE=registry-read

aws --version
aws agent-registry-control list-registries --generate-cli-skeleton input
aws configure list-profiles
```

`agent-registry-control` が `Invalid choice` になる場合は AWS CLI v2 を更新します。`bedrock-agentcore-control` の結果で `agent-registry` 名前空間の確認を済ませないでください。[AWS CLI の list-registries](https://docs.aws.amazon.com/cli/latest/reference/agent-registry-control/list-registries.html)

2. 新規に作る場合は、次のコマンドで対話設定を開始します。

```bash
aws configure sso --profile "$READ_PROFILE"
```

| プロンプトまたは画面 | 入力・選択する内容 |
| --- | --- |
| `SSO session name (Recommended)` | セッションに `ava-org` などの名前を付けます。既存セッションを使う場合はその名前を入力します。 |
| `SSO start URL` | 確認済みの組織の SSO 開始 URL を入力します。 |
| `SSO region` | IAM Identity Center インスタンスのリージョンを入力します。 |
| `SSO registration scopes` | `sso:account:access` を入力します。既定値に表示されていれば Enter で進みます。 |
| ブラウザーでのサインイン | 自分の組織アカウントでログインし、AWS CLI へのアクセスを承認します。 |
| AWS アカウントの選択 | 台帳で確認した Registry 所有アカウントの ID を選びます。 |
| IAM ロールの選択 | 事前に確認した読み取り用の権限名を選びます。候補が 1 つの場合は自動選択されるため、表示された値を確認します。 |
| `Default client Region` | Registry の読み取り用には `us-east-1` を入力します。Cognito や secret 用を別に作る場合は、そのサービスのリージョンを指定できます。 |
| `CLI default output format` | `json` を入力します。 |

対話設定は通常 `~/.aws/config` に `[profile registry-read]` と `[sso-session ava-org]` のような設定を書き込みます。`sso_account_id` と `sso_role_name` はプロファイル側に、`sso_start_url` と `sso_region` はセッション側に入ります。`--profile` に渡すのは `registry-read` のようなプロファイル名です。SSO ログインで短期認証情報を取得するため、アクセスキーの発行は不要です。[AWS CLI の IAM Identity Center 設定](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html) · [AWS CLI の設定ファイル](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html)

3. 設定したプロファイルでログインします。STS の `Account` が Registry の所有アカウントと一致し、`Arn` の `assumed-role/` に続くロールが読み取り用の割り当てと合うことを確認します。別のアカウントやロールなら設定を見直し、値が一致してから手順 2 へ進みます。

```bash
aws configure get sso_account_id --profile "$READ_PROFILE"
aws configure get sso_role_name --profile "$READ_PROFILE"
aws sso login --profile "$READ_PROFILE"
aws sts get-caller-identity --profile "$READ_PROFILE" --region "$REGION" --output json
```

- アカウントや権限が候補に出ない場合は、AWS アクセスポータルでも割り当てを確認し、表示がなければ IAM Identity Center 管理者に問い合わせます。プロファイルを作成しても AWS 側の権限は増えません。
- SSO セッションの期限切れが表示されたら、対象プロファイルで `aws sso login --profile "$READ_PROFILE"` を再実行します。ログインが成功して `AccessDenied` が返る場合は、STS のアカウント・ロールと、その操作の権限を確認します。
- CLI を実行する端末にブラウザーがなく、組織がデバイスコード方式を許可している場合は、`aws configure sso --profile "$READ_PROFILE" --use-device-code --no-browser` と `aws sso login --profile "$READ_PROFILE" --use-device-code --no-browser` を使います。表示された確認 URL をブラウザーで開き、端末に表示されたコードで認証します。[AWS CLI の sso login](https://docs.aws.amazon.com/cli/latest/reference/sso/login.html)

### IAM ユーザーを作成する

通常の担当者には IAM Identity Center を使います。組織の方針で個別の IAM ユーザーが必要な場合や、IAM Identity Center 障害時にも緊急対応する場合は、IAM 管理者が次の手順で作成します。

1. IAM 管理者が作成先の AWS アカウントにサインインし、**IAM** → **Users** → **Create user** を開きます。利用者または緊急用途を識別できるユーザー名を入力し、作成先アカウントと利用責任者を台帳に記録します。
2. コンソールを利用する場合は **Provide user access to the AWS Management Console** を選び、**I want to create an IAM user** を選択します。自動生成の初期パスワードを選びます。個人に割り当てる場合は初回サインイン時のパスワード変更を指定し、アカウントのパスワードポリシーまたは IAM ポリシーで本人に変更が許可されていることを確認します。
3. **Set permissions** では Registry の権限を直接追加せずに進み、内容を確認して **Create user** を選びます。作成されたユーザーの IAM ARN を控えます。この ARN を後述のロールの信頼ポリシーに使います。
4. コンソールへのサインイン URL と初期パスワードを、組織の認証情報の受け渡し手順に従って引き渡します。緊急利用のために作成した場合は、利用承認が下りるまで認証情報を指定の保管先で管理します。
5. **IAM** → **Users** → 対象ユーザー → **Security credentials** の **Assign MFA device** で、組織で利用するデバイスの種類を選びます。認証アプリを使う場合は **Authenticator app** を選び、QR コードを読み取った後に連続する 2 つのコードを入力します。利用者は IAM ユーザー用 URL でサインインし、MFA が機能することを確認します。[認証アプリでの MFA 登録](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_mfa_enable_virtual.html)

コンソールだけを使う場合はアクセスキーの発行は不要です。CLI が必要な場合の設定は後述します。[緊急アクセス用 IAM ユーザーの作成](https://docs.aws.amazon.com/IAM/latest/UserGuide/getting-started-emergency-iam-user.html) · [IAM ユーザーの MFA](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_mfa.html)

### Registry の読み取り用 IAM ロールを作成する

既存の読み取り権限を使えない場合は、IAM 管理者が Registry の所有アカウントに読み取り用ロールを作成します。以下のアカウント ID、Registry ID、IAM ユーザー名、ロール名は例です。実際の値に置き換えます。このロール作成手順は、手順 7 の break-glass ロールにも使います。

1. **IAM** → **Roles** → **Create role** を開きます。**Trusted entity type** で **Custom trust policy** を選択します。IAM ユーザーから引き受ける場合は、次のように `Principal.AWS` に利用を認める IAM ユーザーの ARN を指定します。MFA を使ったセッションだけがロールを引き受けられます。

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": {"AWS": "arn:aws:iam::111122223333:user/registry-operator"},
       "Action": "sts:AssumeRole",
       "Condition": {"Bool": {"aws:MultiFactorAuthPresent": "true"}}
     }]
   }
   ```

   IAM Identity Center の担当者から引き受ける場合は、利用を許可する permission set の割り当てによって作成された `AWSReservedSSO_…` ロールを IAM で開き、IAM ロール ARN を確認します。次の例では `Principal.AWS` にそのロールの所属アカウント、`aws:PrincipalArn` にそのロールの ARN を指定します。ARN は IAM に表示された値を使います。`us-east-1` に IAM Identity Center がある場合、ロールパスにリージョン名は入りません。MFA は IAM Identity Center または組織の IdP で設定します。[permission set の参照方法](https://docs.aws.amazon.com/singlesignon/latest/userguide/referencingpermissionsets.html)

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": {"AWS": "arn:aws:iam::111122223333:root"},
       "Action": "sts:AssumeRole",
       "Condition": {
         "ArnEquals": {
           "aws:PrincipalArn": "arn:aws:iam::111122223333:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_RegistryOps_0123456789abcdef"
         }
       }
     }]
   }
   ```

2. **Add permissions** では既存の承認済みポリシーを選びます。新規にインラインポリシーを追加する場合は、この画面では権限を選ばずに進みます。ロール名を `RegistryPortalReadOnly` として **Create role** を選びます。インラインポリシーを使う場合は、作成後に **Permissions** → **Add permissions** → **Create inline policy** の **JSON** で、次の例の Registry ARN を差し替えて保存します。Cognito と secret は手順 3 の担当者用プロファイルから確認します。

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["agent-registry:ListRegistries", "cloudtrail:LookupEvents"],
         "Resource": "*"
       },
       {
         "Effect": "Allow",
         "Action": ["agent-registry:GetRegistry", "agent-registry:ListRegistryRecords"],
         "Resource": "arn:aws:agent-registry:us-east-1:111122223333:registry/Registry0001"
       }
     ]
   }
   ```

3. 作成したロールの ARN を控えます。IAM ユーザーから利用する場合は、**IAM** → **Users** → 対象ユーザー → **Permissions** のインラインポリシーに次の許可を追加します。IAM Identity Center を経由する場合は、管理者が引き受け元の permission set に同じ許可を追加し、対象アカウントへの反映を確認します。`Resource` には実際に作成したロールの ARN を指定します。

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": "sts:AssumeRole",
       "Resource": "arn:aws:iam::111122223333:role/RegistryPortalReadOnly"
     }]
   }
   ```

4. IAM ユーザーでコンソールを使う場合は、右上のアカウントメニューから **Switch role** を開き、ロールの所属アカウント ID とロール名を入力します。CLI を使う場合は、次の手順でロール用プロファイルを設定します。作成後に、引き受け先のアカウントとロール名、対象 Registry を読み取れることを確認します。[カスタム信頼ポリシーを使うロールの作成](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-custom.html) · [ロール切り替えの許可](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_permissions-to-switch.html) · [コンソールでのロール切り替え](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_switch-role-console.html)

### IAM ロールを使う AWS CLI プロファイルを作成する

1. IAM Identity Center から引き受ける場合は、手順 1 の `aws configure sso --profile` で、引き受け元の permission set を選んだプロファイルを作成し、ログインします。以下の例では引き受け元の名前を `registry-operator-sso` とします。
2. `~/.aws/config` にロール用プロファイルを追加します。`role_arn` と `source_profile` を実際の値に置き換えます。作成した名前を手順 2 以降の `READ_PROFILE` に使います。

   ```ini
   [profile registry-read-role]
   role_arn = arn:aws:iam::111122223333:role/RegistryPortalReadOnly
   source_profile = registry-operator-sso
   region = us-east-1
   output = json
   ```

3. IAM ユーザーから CLI で引き受ける必要がある場合は、IAM 管理者がアクセスキーの使用を承認したうえで、**IAM** → **Users** → 対象ユーザー → **Security credentials** → **Create access key** から発行します。作業端末で `aws configure --profile registry-iam-user` を実行し、対話入力で設定します。ロール用プロファイルの `source_profile` を `registry-iam-user` に変更し、`mfa_serial = arn:aws:iam::111122223333:mfa/registry-operator` のように、そのユーザーに登録した CLI 対応の MFA デバイスの ARN を指定します。CLI が求めた時点で MFA コードを入力します。キーは組織の管理手順に従って保管し、不要になったら無効化します。[アクセスキーの作成・管理](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-keys-admin-managed.html) · [AWS CLI のロール用プロファイルと MFA](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-role.html)
4. ロール用プロファイルで STS を呼び、`Account` が引き受け先のアカウント、`Arn` に `assumed-role/RegistryPortalReadOnly/` が含まれることを確認します。IAM Identity Center セッションの期限が切れた場合は引き受け元のプロファイルで `aws sso login` を再実行します。ロールの引き受けが拒否された場合は、引き受け元の `sts:AssumeRole` 許可とロールの信頼ポリシーを照合します。

   ```bash
   READ_PROFILE=registry-read-role
   aws sts get-caller-identity --profile "$READ_PROFILE" --region "$REGION" --output json
   ```

## 2. Registry の識別子、自動承認、custom metadata schema を確認する

1. `us-east-1` で Registry の一覧を取得します。候補を台帳の名前または ID と照合します。候補がない場合や複数あって特定できない場合は、アカウントとロールを確認し、管理者に対象を特定してもらいます。

   ```bash
   aws agent-registry-control list-registries \
     --profile "$READ_PROFILE" --region "$REGION" --output json \
     --query 'registries[].{name:name,id:registryId,arn:registryArn,status:status}'
   ```

2. 対象の ID を設定し、詳細を取得します。`registryArn` が `arn:aws:agent-registry:us-east-1:<所有アカウントID>:registry/<Registry ID>` の形で、末尾が `registryId` と一致し、`status` が `READY` であることを確認します。名前、ARN、ID、リージョン、確認時刻を記録します。

   ```bash
   REGISTRY_ID='一覧で確認した Registry ID'
   REGISTRY_JSON=$(aws agent-registry-control get-registry \
     --registry-id "$REGISTRY_ID" \
     --profile "$READ_PROFILE" --region "$REGION" --output json)

   jq '{name, registryId, registryArn, status, approvalConfiguration}' <<< "$REGISTRY_JSON"
   ```

3. `approvalConfiguration.autoApprovalRules` を確認します。AWS の正常な応答でこの設定が省略されているか、値が `[]` なら、申請は手動審査です。`APPROVE_ALL` があれば自動承認が有効です。取得失敗や解釈できない設定は未確認として記録します。自動承認が有効な場合は差分を親 Issue に報告し、申請を伴う後続の検証を保留します。確認のためのレコード作成や申請は不要です。[AWS CLI の get-registry](https://docs.aws.amazon.com/cli/latest/reference/agent-registry-control/get-registry.html)
4. 共通の `defaultSchema` と `AGENT` の `recordTypeSchemaOverrides` を確認します。両方の `properties` と `required` から、`AGENT` が使うフィールド名、型、必須かどうかを記録します。`isA2A` は有無、定義場所、型、必須かどうかを明記します。`null` は該当スキーマの未設定を表します。

   ```bash
   jq '
     .customMetadataSchemaConfiguration as $schema
     | {
         common: ($schema.defaultSchema | if . == null then null else fromjson end),
         agent: ([$schema.recordTypeSchemaOverrides[]?
                  | select(.recordType == "AGENT") | .schema | fromjson] | first)
       }
   ' <<< "$REGISTRY_JSON"
   ```

`isA2A` の設計上の定義は `AGENT` 専用の必須 Boolean です。現状との差分を記録し、スキーマ追加が必要なら IaC の管理先を親 Issue で確認します。[ADR-0006](../adr/0006-a2a-search-metadata-as-copy.md) · [AWS の custom metadata schema](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-custom-metadata-schema.html)

## 3. 発見用 M2M の Cognito 設定と secret の保管先を確認する

1. `discoveryConfiguration.authorizerType` が `CUSTOM_JWT` であることを確認します。`discoveryUrl` を Cognito User Pool の issuer に対応する OIDC のメタデータ URL と照合し、リージョンと User Pool ID を確認します。Cognito コンソールで、その ID の User Pool を開きます。

   ```bash
   jq '.discoveryConfiguration | {
         authorizerType,
         jwt: (.authorizerConfiguration.customJWTAuthorizer
               | {discoveryUrl, allowedClients, allowedAudience, allowedScopes})
       }' <<< "$REGISTRY_JSON"
   ```

2. Cognito の **User pools** で対象プールを開き、**App clients** で既存の発見用クライアントを確認します。`allowedClients` に制約がある場合は、`client_id` が含まれることを確認します。認可が audience や追加クレームで制限されている場合は、その条件も発見用 M2M の設定と照合します。候補が複数ある場合は、既存の発見用アプリの構成や運用担当者を根拠に特定します。
3. 手順 1 で用意した `COGNITO_PROFILE` を指定します。STS の `Account` と `Arn` を User Pool の所有アカウントと読み取り用ロールに照合してから、クライアント名、ID、フロー、scope を確認します。既存の構成で Cognito のドメイン経由の `client_credentials` を使っている場合、`AllowedOAuthFlowsUserPoolClient` が `true` で、`AllowedOAuthFlows` に `client_credentials` があることを確認します。Cognito が発行可能な `AllowedOAuthScopes` と、Registry が受け付ける `allowedScopes` をそれぞれ記録します。後者が省略されていれば「Registry 側の scope 制限なし」と記録します。

   ```bash
   COGNITO_PROFILE=registry-cognito-read
   COGNITO_REGION='User Pool のリージョン'
   USER_POOL_ID='確認した User Pool ID'
   DISCOVERY_CLIENT_ID='確認した発見用 client_id'

   aws sso login --profile "$COGNITO_PROFILE"
   aws sts get-caller-identity --profile "$COGNITO_PROFILE" --region "$COGNITO_REGION"
   aws cognito-idp describe-user-pool-client \
     --user-pool-id "$USER_POOL_ID" --client-id "$DISCOVERY_CLIENT_ID" \
     --profile "$COGNITO_PROFILE" --region "$COGNITO_REGION" --output json \
     --query 'UserPoolClient.{pool:UserPoolId,id:ClientId,name:ClientName,oauth:AllowedOAuthFlowsUserPoolClient,flows:AllowedOAuthFlows,scopes:AllowedOAuthScopes}'
   ```

4. 既存のクライアント設定または構成台帳で、当該 `client_id` の secret が参照する ARN を特定します。Secrets Manager を使用している場合は、手順 1 で用意した `SECRET_PROFILE` でログインし、STS で所有アカウントとロールを照合します。`describe-secret` で保管先 ARN と削除予定の有無を確認します。別の保管サービスを使っている場合は、管理者がそのサービスで保管先 ARN を確認します。

   ```bash
   SECRET_PROFILE=registry-secret-metadata
   SECRET_REGION='secret のリージョン'
   SECRET_ARN='既存設定が参照する secret ARN'

   aws sso login --profile "$SECRET_PROFILE"
   aws sts get-caller-identity --profile "$SECRET_PROFILE" --region "$SECRET_REGION"
   aws secretsmanager describe-secret --secret-id "$SECRET_ARN" \
     --profile "$SECRET_PROFILE" --region "$SECRET_REGION" --output json \
     --query '{arn:ARN,deletedDate:DeletedDate}'
   ```

ARN が不明か、クライアントとの対応を確認できなければ未確認とします。この設定確認では secret 値の取得は不要です。`describe-user-pool-client` は `ClientSecret` を含み得るため、上の `--query` を付け、`--debug` は使いません。[Cognito の app client](https://docs.aws.amazon.com/cli/latest/reference/cognito-idp/describe-user-pool-client.html) · [Secrets Manager の describe-secret](https://docs.aws.amazon.com/cli/latest/reference/secretsmanager/describe-secret.html)

## 4. 全レコードの件数と作成経路を調べる

1. 管理 API で全状態・全種別を列挙します。AWS CLI の自動ページングを使います。`--no-paginate`、`--max-items`、状態フィルターは付けません。取得時刻と `recordId` ごとの一覧を作業記録に残します。

   ```bash
   RECORDS_JSON=$(aws agent-registry-control list-registry-records \
     --registry-id "$REGISTRY_ID" \
     --profile "$READ_PROFILE" --region "$REGION" --output json)

   jq -e '.registryRecords | if type == "array" then {
     total: (map(.recordId) | unique | length),
     duplicateIds: (group_by(.recordId) | map(select(length > 1) | .[0].recordId)),
     byTypeAndStatus: (group_by([.recordType, .status])
       | map({type: .[0].recordType, status: .[0].status, count: length}))
   } else error("レコード一覧を取得できていません") end' <<< "$RECORDS_JSON"

   jq -r '.registryRecords[] | [
     .recordId, .recordType, .status, .createdAt, .createdBy,
     .createdByAutoDetection, (.provenanceSummaryList // [] | tojson)
   ] | @tsv' <<< "$RECORDS_JSON"
   ```

2. 同じアカウント・リージョンの CloudTrail で **Event history** を開き、**Event name** を `CreateRegistryRecord` に絞ります。成功したイベントから、対象 Registry と `recordId` が一致するものを確認します。イベント ID、日時、`userIdentity`、`userAgent` を組織 trail や既存のデプロイ記録と照合します。履歴の対象期間は直近 90 日です。古いレコードは組織 trail の保存ログや当時の変更記録を確認します。
3. レコード単位で、AWS の自動検出、コンソール、CLI、IaC、アプリ経由など、証跡で確認できた作成経路を記録します。自動検出は `createdByAutoDetection` と `provenanceSummaryList` が根拠になります。経路を裏付ける記録がなければ「不明」とし、参照した期間と資料を残します。重複 ID が出た場合は、変更が落ち着いた時点で全件を取り直します。経路別の件数を合計し、全件数と一致することを確認します。[管理 API の一覧](https://docs.aws.amazon.com/cli/latest/reference/agent-registry-control/list-registry-records.html) · [Registry の CloudTrail 記録](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-cloudtrail-integration.html) · [CloudTrail イベント履歴の対象期間](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/view-cloudtrail-events.html)

## 5. 公開中の Agent を Agent Card の有無で集計する

公開中の版は発見用 API で確認します。管理 API が返す現行レコードに下書きや審査中の版があっても、発見用 API の公開版を集計に使います。発見用 M2M の運用担当者が、既存の発行手順で対象 Registry のアクセス用 JWT を用意します。Cognito ドメイン方式では、対象クライアントから User Pool ドメインの `/oauth2/token` に `grant_type=client_credentials` と手順 3 で照合した `scope` を指定して発行します。token は管理担当者の端末でのみ扱います。[Cognito の token endpoint](https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html)

次のコマンドを対話端末で実行し、入力欄にアクセス用 JWT を貼り付けます。入力は画面に表示されません。公開用 API の全ページから `AGENT` の ID と版を集め、公開用の詳細 API で `descriptors.a2aAgentCard` を確認します。実行結果には件数だけが表示されます。

```bash
python3 - "$REGISTRY_ID" <<'PY'
import getpass
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


registry_id = sys.argv[1]
if not re.fullmatch(r"[A-Za-z0-9]{12,16}", registry_id):
    raise SystemExit("Registry ID を確認してください。")
token = getpass.getpass("発見用 M2M のアクセス用 JWT を入力してください。 ").strip()
if not token:
    raise SystemExit("JWT が入力されていません。")

endpoint = "https://agent-registry.us-east-1.api.aws"
opener = build_opener(NoRedirect)


def post(path, payload):
    request = Request(
        endpoint + path,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise SystemExit(f"発見用 API が HTTP {error.code} を返しました。") from None
    except (URLError, TimeoutError, ValueError):
        raise SystemExit("発見用 API に接続できないか、応答を読み取れません。") from None


published_versions = {}
params = {"maxResults": 100, "filters": [{"name": "recordType", "values": ["AGENT"]}]}
seen_tokens = set()
while True:
    page = post(f"/registries/{registry_id}/discoverable-records-list", params)
    for record in page["registryRecords"]:
        record_id = record["recordId"]
        if record["recordType"] != "AGENT" or record_id in published_versions:
            raise SystemExit("一覧に想定外の種別または重複がありました。全件を取り直してください。")
        published_versions[record_id] = record["recordVersion"]
    next_token = page.get("nextToken")
    if not next_token:
        break
    if next_token in seen_tokens:
        raise SystemExit("ページトークンが重複しました。全件を取り直してください。")
    seen_tokens.add(next_token)
    params["nextToken"] = next_token

record_ids = list(published_versions)
with_card = 0
for start in range(0, len(record_ids), 100):
    batch = record_ids[start : start + 100]
    result = post("/discoverable-records-batch", {
        "entries": [{"registryId": registry_id, "recordIds": batch}],
    })
    details = result["registryRecords"]
    if result.get("errors") or sorted(r["recordId"] for r in details) != sorted(batch):
        raise SystemExit("一部の公開レコードを取得できません。権限と公開状況を確認してください。")
    for record in details:
        if record["recordVersion"] != published_versions[record["recordId"]]:
            raise SystemExit("集計中に公開版が変わりました。全件を取り直してください。")
        descriptors = record["descriptors"]
        if not isinstance(descriptors, dict) or not descriptors:
            raise SystemExit("公開版の descriptor を確認できません。")
        with_card += int(descriptors.get("a2aAgentCard") is not None)

print(json.dumps({
    "公開中のAGENT": len(record_ids),
    "Agent Cardあり": with_card,
    "Agent Cardなし": len(record_ids) - with_card,
}, ensure_ascii=False, indent=2))
PY
```

取得時刻と 3 つの件数を記録します。JWT の期限切れは再発行し、401・403 が続く場合は issuer、クライアント、scope、追加クレームを見直します。取得エラーや欠落があれば集計を未確認とします。[公開用の一覧 API](https://docs.aws.amazon.com/agent-registry/latest/APIReference/API_ListDiscoverableRegistryRecords.html) · [公開用の詳細 API](https://docs.aws.amazon.com/agent-registry/latest/APIReference/API_BatchGetDiscoverableRegistryRecord.html) · [ADR-0006](../adr/0006-a2a-search-metadata-as-copy.md) · [ADR-0007](../adr/0007-published-snapshot-for-detail.md)

## 6. `RegistryPortalDeployer` を作成して拒否設定を検証する

1. IAM Identity Center 管理者が、組織のインスタンスを管理するアカウントとリージョンで **Permission sets** → **Create permission set** → **Custom permission set** を開きます。`RegistryPortalDeployer` が存在する場合は既存の設定と割り当てを確認します。
2. 次の明示的な拒否をインラインポリシーに追加します。既存のインラインポリシーがある場合は `Statement` に追加します。デプロイに必要な `Allow` は、ポータルのデプロイ対象をもとに IAM 管理者が承認したものを指定します。この拒否ポリシー単体ではデプロイ権限は付与されません。

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "DenyRegistryAndRecordWrites",
         "Effect": "Deny",
         "Action": [
           "agent-registry:CreateRegistry",
           "agent-registry:UpdateRegistry",
           "agent-registry:DeleteRegistry",
           "agent-registry:CreateRegistryRecord",
           "agent-registry:UpdateRegistryRecord",
           "agent-registry:DeleteRegistryRecord",
           "agent-registry:SubmitRegistryRecordForApproval",
           "agent-registry:UpdateRegistryRecordStatus"
         ],
         "Resource": "*"
       },
       {
         "Sid": "DenyRegistryRecordTagWrites",
         "Effect": "Deny",
         "Action": ["agent-registry:TagResource", "agent-registry:UntagResource"],
         "Resource": "arn:aws:agent-registry:*:*:registry/*/record/*"
       }
     ]
   }
   ```

3. permission set 名を `RegistryPortalDeployer` にして保存します。**AWS accounts** で対象アカウントを開き、デプロイ担当グループにこの permission set を割り当てます。既存の割り当てを更新した場合は、**Permission sets** で対象を選び、**Accounts** タブで対象アカウントへの変更の反映を確認します。更新が必要な場合は **Update** を選択します。permission set ARN、対象アカウント、割り当て先グループ、反映結果を記録します。[permission set の作成と割り当て](https://docs.aws.amazon.com/singlesignon/latest/userguide/howtocreatepermissionset.html) · [既存の permission set の確認と更新](https://docs.aws.amazon.com/singlesignon/latest/userguide/howtoviewandchangepermissionset.html)
4. デプロイ担当者が、手順 1 の対話設定でデプロイ用プロファイルを作成します。デプロイ先アカウントと `RegistryPortalDeployer` を選び、STS の `Account` と `Arn` を照合します。`Arn` のロール名は `AWSReservedSSO_RegistryPortalDeployer_` で始まります。

   ```bash
   DEPLOY_PROFILE=registry-deploy
   aws configure sso --profile "$DEPLOY_PROFILE"
   aws sso login --profile "$DEPLOY_PROFILE"
   aws sts get-caller-identity --profile "$DEPLOY_PROFILE" --region "$REGION" --output json
   ```

5. IAM 管理者は対象アカウントの **IAM Roles** で `AWSReservedSSO_RegistryPortalDeployer_` から始まるロールの IAM ARN を控えます。IAM Policy Simulator でそのロールを指定し、次のアクションがすべて明示的な拒否になることを確認します。

   | シミュレーションのリソース | 確認する `agent-registry` アクション |
   | --- | --- |
   | `*` | `CreateRegistry` |
   | 確認した Registry ARN | `UpdateRegistry`、`DeleteRegistry`、`CreateRegistryRecord` |
   | `<Registry ARN>/record/<record ID>` | `UpdateRegistryRecord`、`DeleteRegistryRecord`、`SubmitRegistryRecordForApproval`、`UpdateRegistryRecordStatus`、`TagResource`、`UntagResource` |

   実レコードが 0 件なら、シミュレーション用に仮の ID `Example00001` を使えます。CLI で検証する場合は、手順 1 で用意した `IAM_ADMIN_PROFILE` でログインします。STS のアカウントとロールが合っていればシミュレーションを実行し、すべての `Decision` が `explicitDeny` であることを記録します。

   ```bash
   IAM_ADMIN_PROFILE=registry-iam-admin
   DEPLOYER_ROLE_ARN='IAM で確認したデプロイ用ロールの ARN'
   REGISTRY_ARN=$(jq -er '.registryArn' <<< "$REGISTRY_JSON")
   SIMULATION_RECORD_ID='実レコードの ID または Example00001'

   aws sso login --profile "$IAM_ADMIN_PROFILE"
   aws sts get-caller-identity --profile "$IAM_ADMIN_PROFILE" --region "$REGION" --output json

   simulate_deny() {
     local resource_arn=$1
     shift
     aws iam simulate-principal-policy --profile "$IAM_ADMIN_PROFILE" \
       --policy-source-arn "$DEPLOYER_ROLE_ARN" --resource-arns "$resource_arn" \
       --action-names "$@" \
       --query 'EvaluationResults[].{Action:EvalActionName,Resource:EvalResourceName,Decision:EvalDecision}' \
       --output table
   }

   simulate_deny '*' agent-registry:CreateRegistry
   simulate_deny "$REGISTRY_ARN" agent-registry:UpdateRegistry \
     agent-registry:DeleteRegistry agent-registry:CreateRegistryRecord
   simulate_deny "$REGISTRY_ARN/record/$SIMULATION_RECORD_ID" \
     agent-registry:UpdateRegistryRecord agent-registry:DeleteRegistryRecord \
     agent-registry:SubmitRegistryRecordForApproval agent-registry:UpdateRegistryRecordStatus \
     agent-registry:TagResource agent-registry:UntagResource
   ```

   `implicitDeny` の場合は、ポリシーのアクション・リソース・反映状況を見直します。Registry の更新や削除を実行せずに確認できます。[Registry の IAM アクションと ARN](https://docs.aws.amazon.com/service-authorization/latest/reference/list_agent-registry.html) · [IAM Policy Simulator](https://docs.aws.amazon.com/cli/latest/reference/iam/simulate-principal-policy.html) · [ADR-0004](../adr/0004-no-aws-deployment-from-ci.md)

## 7. break-glass のロールと担当者を決める

1. AWS 管理者が、対象 Registry への緊急対応に利用する管理ロールを決めます。ロール ARN、一次担当者と代替担当者、承認者、CloudTrail とポータル通知の確認担当者、連絡先を運用台帳に記録します。`RegistryPortalDeployer` とは別のロールを指定します。
   - 新設する場合は、Registry の所有アカウントで[手順 1 のロール作成手順](#registry-の読み取り用-iam-ロールを作成する)を使います。ロール名の例は `RegistryPortalBreakGlass` です。信頼ポリシーには緊急利用を許可した引き受け元を指定し、その引き受け元に新しいロール ARN への `sts:AssumeRole` を許可します。IAM Identity Center の障害時にも使う場合は、利用を許可した IAM ユーザーと MFA を信頼ポリシーで指定します。
   - 権限ポリシーには、承認された操作とリソース ARN を設定します。たとえば `isA2A` の補完のためにレコード更新が認められた場合は、対象 Registry の配下のレコード ARN に `agent-registry:GetRegistryRecord` と `agent-registry:UpdateRegistryRecord` を許可します。対象を特定できる場合は `<Registry ARN>/record/<record ID>` を使います。Registry 自体の設定変更や削除を必要とする場合は、別途、対象操作と権限を確定します。[Registry の IAM アクションと ARN](https://docs.aws.amazon.com/service-authorization/latest/reference/list_agent-registry.html)
   - **IAM** → **Roles** → 作成したロールで、**Trust relationships**、**Permissions**、最大セッション期間を確認します。CLI を使う担当者は[ロール用プロファイルの手順](#iam-ロールを使う-aws-cli-プロファイルを作成する)で `registry-break-glass` などの名前を付け、`role_arn` に緊急ロールの ARN を指定します。承認された担当者が短期セッションを取得できることと、STS が対象の緊急ロールを返すことを確認します。
2. 利用時の申請に、障害・変更管理のチケット番号、利用理由、対象 record ID、公開版、予定操作、担当者、実施時間、復旧または中止の条件を記録する手順を決めます。承認後、担当者は組織の手順で短期セッションを取得し、`aws sts get-caller-identity` で対象アカウントと緊急ロールであることを照合します。準備時は承認経路とロールの取得可否まで確認します。
3. `isA2A` の通常の補完は [Issue #16](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/16) の取り込み後、所有チームの編集・申請・公開承認を通じて行います。ポータル外で補完する必要がある場合は、承認を得た担当者が公開版と編集中の版を読み取り、`a2aAgentCard` の有無から期待する Boolean 値を決めます。対象版、他の metadata の保持、公開状態への影響、変更前後の確認方法をチケットに記録します。公開版だけを対象にできるか確認できない場合は中止し、通常の手順または親 Issue で合意した方法を使います。
4. 実際に直接変更した場合は、担当者が対象の版と metadata を再取得し、CloudTrail のイベント ID をチケットに追記します。監査担当者は操作主体、対象、変更時刻を照合し、ポータルの「ポータル外の変更」通知を Admin で確認します。metadata の変更だけで状態変化イベントが発行されず通知を確認できない場合は、Admin に直接連絡し、CloudTrail のイベント ID と通知の確認結果を記録します。作業終了時に緊急アクセスを解除または失効させます。[ADR-0001](../adr/0001-registry-writes-only-through-portal.md) · [A2A 検索を有効にする手順](../../applications/registry_portal/README.md#a2a-検索を有効にする)

## 8. 証跡を残し、前提との差分を報告する

作業記録には、確認日時と担当者のほか、次を残します。

- [ ] Registry の名前、名前空間、リージョン、ARN、ID、状態、自動承認の判定と根拠。
- [ ] Cognito User Pool ID、発見用 `client_id`、secret 保管先 ARN、Cognito と Registry の scope、照合結果。
- [ ] 管理 API の全件数、状態・種別ごとの件数、作成経路別の件数、根拠と不明件数。
- [ ] `AGENT` が使う custom metadata フィールド、`isA2A` の有無・定義場所・型・必須かどうか、公開中の Agent Card あり・なしの件数。
- [ ] `RegistryPortalDeployer` の ARN、対象アカウント、割り当て先、反映結果、明示的な拒否の検証結果。
- [ ] break-glass のロール ARN、担当者、承認・利用・監査の手順と保管先。`isA2A` の例外対応も含める。

前提と違う点があれば、[親 Issue #1](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/1) に「想定」「確認結果」「後続作業への影響」「判断または対応が必要な事項」をコメントします。照会に失敗した項目は未確認として、確認を依頼する担当者を記録します。Issue #2 では、確認済みの項目をチェックし、作業記録の参照先と残件を示します。secret 値、JWT、権限者の個人情報、非公開のレコード本体は組織の共有ルールに従って扱い、Issue には貼り付けません。
