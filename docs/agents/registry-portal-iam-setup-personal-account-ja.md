# 個人 AWS アカウントで Registry ポータル用の IAM ユーザーとロールを作成する

[Issue #2](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/2) の事前準備のうち、IAM ユーザーと IAM ロールの作成だけを扱う手順書です。IAM Identity Center を使わず、個人の AWS アカウント 1 つで作業する場合を対象にします。操作は日本語表示の AWS マネジメントコンソールで行います。

画面の表記は「日本語（English）」の形で示します。コンソールの更新で日本語の表記が変わることがあります（例: 「許可」と「アクセス許可」）。見つからない場合は英語名を手がかりにしてください。

## 作成するもの

IAM ユーザー 1 人と IAM ロール 3 つを作成します。

| 種類 | 名前 | 用途 | 権限 |
| --- | --- | --- | --- |
| IAM ユーザー | `registry-operator` | 日常のサインイン用。ルートユーザーの代わりに使います。 | 下の 3 ロールへの `sts:AssumeRole` だけ。 |
| IAM ロール | `RegistryPortalReadOnly` | Issue #2 の確認作業。Registry、Cognito、secret のメタデータ、CloudTrail を読み取り、拒否設定をシミュレーションします。 | 読み取りと CloudShell。 |
| IAM ロール | `RegistryPortalDeployer` | ポータルのデプロイ（Terraform）。Issue #2 の permission set `RegistryPortalDeployer` の代わりです。 | デプロイ用の許可と、Registry への書き込みの明示的な拒否。 |
| IAM ロール | `RegistryPortalBreakGlass` | 緊急時に Registry のレコードを直接変更する管理ロール。 | 対象 Registry の読み取りとレコードの更新。 |

次のものは作成しません。

- **管理 Lambda、決裁 Lambda、イベント処理 Lambda の実行ロール。** ポータルのデプロイで Terraform が作成します。
- **ポータル利用者、発見用 M2M、CI 用の IAM ユーザーとロール。** 認証には Cognito を使います。
- **GitHub Actions から AWS へデプロイするロール。** デプロイは人間がローカル端末から行います。[ADR-0004](../adr/0004-no-aws-deployment-from-ci.md)

ロールは使うときだけ引き受けます。操作の種類ごとにロールを切り替えることで誤操作を防ぎ、CloudTrail でどのロールの操作かを区別できます。1 人で 3 つのロールをすべて使えるため、担当者の分離にはなりません。

## 0. 作業前に確認する

- **IAM を管理できるサインイン。** 管理者権限の IAM ユーザーがなければ、この手順の間だけルートユーザーでサインインします。ルートユーザーに MFA を設定済みであることを確認します。
- **12 桁の AWS アカウント ID。** コンソール右上のアカウント名を選ぶと表示されます。この手順書では `111122223333` と書きます。JSON を貼り付ける前に、すべて実際の ID に置き換えます。
- **MFA 用の認証アプリ。** スマートフォンの認証アプリなどを用意します。

IAM はグローバルサービスのため、作業中のリージョンは結果に影響しません。Registry は `us-east-1`（米国東部（バージニア北部））にあります。

## 1. IAM ユーザー `registry-operator` を作成する

1. コンソール上部の検索欄に `IAM` と入力し、**IAM** を開きます。
2. 左のメニューで **ユーザー**（Users）を選び、**ユーザーの作成**（Create user）を選びます。
3. **ユーザー名**（User name）に `registry-operator` を入力します。
4. **AWS マネジメントコンソールへのユーザーアクセスを提供する**（Provide user access to the AWS Management Console）にチェックを入れます。ユーザーの種類を尋ねられたら **IAM ユーザーを作成します**（I want to create an IAM user）を選びます。
5. **コンソールパスワード**（Console password）で **カスタムパスワード**（Custom password）を選び、パスワードを入力します。自分で使うユーザーのため、**ユーザーは次回サインイン時に新しいパスワードを作成する必要があります**（Users must create a new password at next sign-in）のチェックを外します。**次へ**（Next）を選びます。
6. **許可を設定**（Set permissions）では何も選ばずに **次へ** を選びます。権限は手順 5 で追加します。
7. 内容を確認し、**ユーザーの作成**（Create user）を選びます。
8. 完了画面の **コンソールサインイン URL**（Console sign-in URL）を控えます。形式は `https://111122223333.signin.aws.amazon.com/console` です。

### MFA を設定する

ロールの信頼ポリシーで MFA を必須にします。MFA を設定しないとロールを使えません。

1. **ユーザー** → `registry-operator` → **セキュリティ認証情報**（Security credentials）タブを開きます。
2. **多要素認証 (MFA)**（Multi-factor authentication (MFA)）の **MFA デバイスの割り当て**（Assign MFA device）を選びます。
3. **デバイス名**（Device name）に `registry-operator` を入力し、**認証アプリケーション**（Authenticator app）を選んで **次へ** を選びます。MFA デバイスの ARN は `arn:aws:iam::111122223333:mfa/registry-operator` になります。
4. **QR コードを表示**（Show QR code）を選び、認証アプリで読み取ります。アプリに表示される連続した 2 つのコードを **MFA コード 1** と **MFA コード 2** に入力し、**MFA を追加**（Add MFA）を選びます。

ユーザーの **概要**（Summary）にある ARN `arn:aws:iam::111122223333:user/registry-operator` を控えます。

[IAM ユーザーの作成](https://docs.aws.amazon.com/ja_jp/IAM/latest/UserGuide/id_users_create.html) · [認証アプリでの MFA 登録](https://docs.aws.amazon.com/ja_jp/IAM/latest/UserGuide/id_credentials_mfa_enable_virtual.html)

## 2. ロールの作成に共通する操作

手順 3〜4 の 3 つのロールは、同じ操作で作成します。

### 信頼ポリシー

3 つのロールとも次の信頼ポリシーを使います。`registry-operator` が MFA でサインインしたセッションだけがロールを引き受けられます。

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

### ロールを作成する

1. **IAM** の左のメニューで **ロール**（Roles）を選び、**ロールを作成**（Create role）を選びます。
2. **信頼されたエンティティタイプ**（Trusted entity type）で **カスタム信頼ポリシー**（Custom trust policy）を選びます。エディターの内容を上の信頼ポリシーに置き換え、**次へ** を選びます。
3. **許可を追加**（Add permissions）で、各手順に書かれた AWS 管理ポリシーにチェックを入れます。指定がなければ何も選びません。**次へ** を選びます。
4. **ロール名**（Role name）と **説明**（Description）を入力し、**ロールを作成**（Create role）を選びます。

### インラインポリシーを追加する

1. **ロール** の一覧で対象のロールを開きます。
2. **許可**（Permissions）タブで **許可を追加**（Add permissions）→ **インラインポリシーを作成**（Create inline policy）を選びます。
3. **ポリシーエディタ**（Policy editor）で **JSON** を選び、各手順の JSON に置き換えて **次へ** を選びます。
4. **ポリシー名**（Policy name）を入力し、**ポリシーの作成**（Create policy）を選びます。

[カスタム信頼ポリシーでのロール作成](https://docs.aws.amazon.com/ja_jp/IAM/latest/UserGuide/id_roles_create_for-custom.html)

## 3. 読み取り用ロール `RegistryPortalReadOnly` を作成する

1. [共通の操作](#ロールを作成する)でロールを作成します。
   - **許可を追加**: `AWSCloudShellFullAccess` にチェックを入れます。コンソールの CloudShell から AWS CLI を使い、アクセスキーを発行せずに済ませるためです。
   - **ロール名**: `RegistryPortalReadOnly`
   - **説明**: `Issue #2 の確認作業用（読み取りのみ）`
2. [インラインポリシーを追加](#インラインポリシーを追加する)します。ポリシー名は `RegistryPortalReadOnlyAccess` です。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListRegistries",
      "Effect": "Allow",
      "Action": "agent-registry:ListRegistries",
      "Resource": "*"
    },
    {
      "Sid": "ReadRegistryAndRecords",
      "Effect": "Allow",
      "Action": [
        "agent-registry:GetRegistry",
        "agent-registry:ListRegistryRecords",
        "agent-registry:GetRegistryRecord",
        "agent-registry:GetResourcePolicy",
        "agent-registry:ListTagsForResource"
      ],
      "Resource": [
        "arn:aws:agent-registry:us-east-1:111122223333:registry/*",
        "arn:aws:agent-registry:us-east-1:111122223333:registry/*/record/*"
      ]
    },
    {
      "Sid": "ReadCognito",
      "Effect": "Allow",
      "Action": ["cognito-idp:Describe*", "cognito-idp:List*"],
      "Resource": "*"
    },
    {
      "Sid": "ReadSecretMetadataOnly",
      "Effect": "Allow",
      "Action": ["secretsmanager:ListSecrets", "secretsmanager:DescribeSecret"],
      "Resource": "*"
    },
    {
      "Sid": "ReadCloudTrailEventHistory",
      "Effect": "Allow",
      "Action": ["cloudtrail:LookupEvents", "cloudtrail:DescribeTrails"],
      "Resource": "*"
    },
    {
      "Sid": "SimulateRolePolicies",
      "Effect": "Allow",
      "Action": [
        "iam:ListRoles",
        "iam:GetRole",
        "iam:ListRolePolicies",
        "iam:GetRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:GetPolicy",
        "iam:GetPolicyVersion",
        "iam:SimulatePrincipalPolicy"
      ],
      "Resource": "*"
    }
  ]
}
```

- `secretsmanager:GetSecretValue` は含めていません。このロールでは secret の値を読めません。
- `cognito-idp:DescribeUserPoolClient` の結果には、クライアントシークレットが含まれることがあります。出力を Issue や共有の場所に貼り付けないでください。
- 発見用 API（`ListDiscoverableRegistryRecords` など）は、発見用 M2M の JWT で呼ぶため、このロールには含めていません。
- Registry の ID は Issue #2 の確認作業で判明するため、アカウント内の Registry 全体（`registry/*`）を対象にしています。

## 4. デプロイ用ロール `RegistryPortalDeployer` を作成する

デプロイでは Lambda、IAM ロール、DynamoDB、API Gateway、Terraform state 用の S3 などを作成するため、広い権限が必要です。ここでは AWS 管理ポリシー `AdministratorAccess` を付け、Registry への書き込みを明示的に拒否します。明示的な拒否は許可より優先されます。

1. [共通の操作](#ロールを作成する)でロールを作成します。
   - **許可を追加**: `AdministratorAccess` にチェックを入れます。
   - **ロール名**: `RegistryPortalDeployer`
   - **説明**: `Registry ポータルのデプロイ用（Registry への書き込みは拒否）`
2. [インラインポリシーを追加](#インラインポリシーを追加する)します。ポリシー名は `RegistryPortalDeployerDeny` です。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyRegistryWrites",
      "Effect": "Deny",
      "Action": [
        "agent-registry:CreateRegistry",
        "agent-registry:UpdateRegistry",
        "agent-registry:DeleteRegistry",
        "agent-registry:PutResourcePolicy",
        "agent-registry:DeleteResourcePolicy",
        "agent-registry:TagResource",
        "agent-registry:UntagResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DenyRegistryRecordWrites",
      "Effect": "Deny",
      "Action": [
        "agent-registry:CreateRegistryRecord",
        "agent-registry:UpdateRegistryRecord",
        "agent-registry:DeleteRegistryRecord",
        "agent-registry:SubmitRegistryRecordForApproval",
        "agent-registry:UpdateRegistryRecordStatus"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DenyChangingProtectedIdentities",
      "Effect": "Deny",
      "Action": [
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:UpdateAssumeRolePolicy",
        "iam:PutRolePermissionsBoundary",
        "iam:DeleteRolePermissionsBoundary",
        "iam:DeleteRole",
        "iam:PutUserPolicy",
        "iam:DeleteUserPolicy",
        "iam:AttachUserPolicy",
        "iam:DetachUserPolicy",
        "iam:CreateAccessKey",
        "iam:CreateLoginProfile",
        "iam:UpdateLoginProfile"
      ],
      "Resource": [
        "arn:aws:iam::111122223333:role/RegistryPortalReadOnly",
        "arn:aws:iam::111122223333:role/RegistryPortalDeployer",
        "arn:aws:iam::111122223333:role/RegistryPortalBreakGlass",
        "arn:aws:iam::111122223333:user/registry-operator"
      ]
    }
  ]
}
```

- `DenyRegistryWrites` と `DenyRegistryRecordWrites` は、Issue #2 の「レコードの書き込み系操作と `UpdateRegistry` / `DeleteRegistry` を Deny」を満たします。リソースポリシーとタグの変更も、Registry の書き込み権限を広げられるため拒否しています。
- `DenyChangingProtectedIdentities` は、デプロイ用ロールが自分の拒否設定を外したり、break-glass ロールや `registry-operator` の権限を変えたりできないようにします。IAM Identity Center の permission set ではこの役割を IAM Identity Center が担うため、個人アカウント向けに追加しています。
- このロールは新しい IAM ロールを作成できます。ポータルの Lambda 実行ロールは Registry への書き込みが必要なため、Terraform が作成する必要があるからです。その結果、Registry に書き込めるロールを別に作ることまでは防げません。デプロイ対象が固まったら、`AdministratorAccess` を必要なサービスだけのポリシーに置き換えるか、作成するロールに permissions boundary を必須にすることを検討します。

[AWS Agent Registry の IAM アクションとリソース](https://docs.aws.amazon.com/service-authorization/latest/reference/list_agent-registry.html) · [ADR-0001](../adr/0001-registry-writes-only-through-portal.md) · [ADR-0004](../adr/0004-no-aws-deployment-from-ci.md)

## 5. break-glass 用ロール `RegistryPortalBreakGlass` を作成する

1. [共通の操作](#ロールを作成する)でロールを作成します。
   - **許可を追加**: 何も選びません。
   - **ロール名**: `RegistryPortalBreakGlass`
   - **説明**: `緊急時に Registry のレコードを直接変更する管理ロール`
2. [インラインポリシーを追加](#インラインポリシーを追加する)します。ポリシー名は `RegistryPortalBreakGlassAccess` です。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListRegistries",
      "Effect": "Allow",
      "Action": "agent-registry:ListRegistries",
      "Resource": "*"
    },
    {
      "Sid": "ReadRegistry",
      "Effect": "Allow",
      "Action": ["agent-registry:GetRegistry", "agent-registry:ListRegistryRecords"],
      "Resource": "arn:aws:agent-registry:us-east-1:111122223333:registry/*"
    },
    {
      "Sid": "ReadAndUpdateRecords",
      "Effect": "Allow",
      "Action": ["agent-registry:GetRegistryRecord", "agent-registry:UpdateRegistryRecord"],
      "Resource": "arn:aws:agent-registry:us-east-1:111122223333:registry/*/record/*"
    }
  ]
}
```

3. ロールの **概要** にある **最大セッション時間**（Maximum session duration）が `1 時間` であることを確認します。

- 許可しているのは、[PR #20 からの引き継ぎ](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/2)にある `isA2A` の補完に必要な、レコードの読み取りと更新だけです。申請、決裁（`UpdateRegistryRecordStatus`）、削除、Registry 本体の変更が必要になったら、対象の操作を決めてから追加します。
- Registry の ID が分かったら、2 か所の `registry/*` を `registry/<Registry ID>` に置き換えます。**許可** タブでポリシー名を選び、**編集**（Edit）から変更します。
- このロールでの直接変更は「ポータル外の変更」として通知・監査の対象になります。[ADR-0001](../adr/0001-registry-writes-only-through-portal.md)

## 6. IAM ユーザーにロールの引き受けを許可する

1. **ユーザー** → `registry-operator` → **許可**（Permissions）タブ → **許可を追加**（Add permissions）→ **インラインポリシーを作成**（Create inline policy）を選びます。
2. **JSON** を選び、次の内容に置き換えて **次へ** を選びます。ポリシー名は `AssumeRegistryPortalRoles` です。

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "AssumeRegistryPortalRoles",
    "Effect": "Allow",
    "Action": "sts:AssumeRole",
    "Resource": [
      "arn:aws:iam::111122223333:role/RegistryPortalReadOnly",
      "arn:aws:iam::111122223333:role/RegistryPortalDeployer",
      "arn:aws:iam::111122223333:role/RegistryPortalBreakGlass"
    ]
  }]
}
```

ここまで済んだら、ルートユーザーまたは管理者ユーザーからサインアウトします。以降の IAM の変更が必要になったときだけ、再びサインインします。

## 7. ロールを切り替えて確認する

1. 手順 1 で控えたコンソールサインイン URL を開き、`registry-operator`、パスワード、MFA コードでサインインします。
2. 右上のユーザー名を選び、**ロールの切り替え**（Switch role）を選びます。
3. **アカウント ID**（Account ID）に 12 桁のアカウント ID、**IAM ロール名**（IAM role name）に `RegistryPortalReadOnly` を入力します。**表示名**（Display name）と **表示色**（Display color）は任意です。**ロールの切り替え** を選びます。
4. 右上の表示がロール名に変わることを確認します。
5. `RegistryPortalDeployer` と `RegistryPortalBreakGlass` でも同じ操作を行い、切り替えられることだけを確認します。Registry は変更しません。
6. 右上のメニューから **スイッチバック**（Switch back）を選び、元のユーザーに戻ります。

切り替えに失敗する場合は、次を確認します。

- MFA コードを入力してサインインしたか。MFA なしのセッションは信頼ポリシーの条件で拒否されます。
- 信頼ポリシーと手順 6 のポリシーで、アカウント ID、ユーザー名、ロール名に誤りがないか。

[コンソールでのロール切り替え](https://docs.aws.amazon.com/ja_jp/IAM/latest/UserGuide/id_roles_use_switch-role-console.html)

## 8. デプロイ用ロールの拒否設定を検証する

Registry を実際に変更せずに、IAM のポリシーシミュレーションで拒否を確認します。

1. `RegistryPortalReadOnly` に切り替え、リージョンを **米国東部（バージニア北部）**（us-east-1）にします。
2. コンソール上部のツールバーにある CloudShell のアイコンを選び、CloudShell を開きます。
3. 次のコマンドを実行します。`ACCOUNT_ID` を実際の値に置き換えます。Registry ID がまだ分からなければ、`REGISTRY_ID` と `RECORD_ID` は仮の値のままで構いません。

```bash
ACCOUNT_ID=111122223333
REGISTRY_ID=ExampleRegistry01
RECORD_ID=Example00001
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/RegistryPortalDeployer"
REGISTRY_ARN="arn:aws:agent-registry:us-east-1:${ACCOUNT_ID}:registry/${REGISTRY_ID}"

aws sts get-caller-identity

simulate() {
  local resource_arn=$1
  shift
  aws iam simulate-principal-policy \
    --policy-source-arn "$ROLE_ARN" --resource-arns "$resource_arn" \
    --action-names "$@" \
    --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table
}

simulate '*' agent-registry:CreateRegistry
simulate "$REGISTRY_ARN" \
  agent-registry:UpdateRegistry agent-registry:DeleteRegistry \
  agent-registry:CreateRegistryRecord agent-registry:PutResourcePolicy \
  agent-registry:DeleteResourcePolicy agent-registry:TagResource agent-registry:UntagResource
simulate "$REGISTRY_ARN/record/$RECORD_ID" \
  agent-registry:UpdateRegistryRecord agent-registry:DeleteRegistryRecord \
  agent-registry:SubmitRegistryRecordForApproval agent-registry:UpdateRegistryRecordStatus
simulate "$REGISTRY_ARN" agent-registry:GetRegistry agent-registry:ListRegistryRecords
```

4. 次の結果になることを確認します。
   - `get-caller-identity` の `Arn` に `assumed-role/RegistryPortalReadOnly/` が含まれる。
   - 最後のコマンド以外は、すべての行が `explicitDeny`。
   - 最後のコマンド（読み取り）は `allowed`。デプロイ用ロールで Registry を読み取れることを示します。

`implicitDeny` の行があれば、`RegistryPortalDeployerDeny` のアクション名とリソースを見直します。

[simulate-principal-policy](https://docs.aws.amazon.com/cli/latest/reference/iam/simulate-principal-policy.html) · [AWS CloudShell](https://docs.aws.amazon.com/ja_jp/cloudshell/latest/userguide/welcome.html)

## 9. Issue #2 の確認作業で AWS CLI を使う

### CloudShell を使う（推奨）

`RegistryPortalReadOnly` に切り替えてから CloudShell を開くと、AWS CLI はそのロールの権限で動きます。アクセスキーは不要です。最初に次のコマンドで Registry 用のコマンドが使えることを確認します。`Invalid choice` になる場合は、CloudShell の AWS CLI が古いため、ローカル端末を使います。

```bash
aws agent-registry-control list-registries --region us-east-1
```

CloudShell は `RegistryPortalReadOnly` でだけ使えます。デプロイと break-glass の操作には、次のローカル端末の設定を使います。

### ローカル端末を使う

ローカル端末の AWS CLI から使うには、`registry-operator` のアクセスキーが必要です。長期の認証情報なので、作業端末の外に出さず、不要になったら削除します。

1. ルートユーザーまたは管理者ユーザーでサインインし、**ユーザー** → `registry-operator` → **セキュリティ認証情報** タブ → **アクセスキーを作成**（Create access key）を選びます。
2. **ユースケース**（Use case）で **コマンドラインインターフェイス (CLI)**（Command Line Interface (CLI)）を選び、確認のチェックを入れて **次へ** を選びます。**アクセスキーを作成** を選びます。
3. 作業端末で次のコマンドを実行し、表示されたアクセスキー ID とシークレットアクセスキーを入力します。シークレットアクセスキーは作成直後の画面でしか表示されません。

   ```bash
   aws configure --profile registry-operator
   ```

4. `~/.aws/config` にロールごとのプロファイルを追加します。アカウント ID は実際の値に置き換えます。

   ```ini
   [profile registry-read]
   role_arn = arn:aws:iam::111122223333:role/RegistryPortalReadOnly
   source_profile = registry-operator
   mfa_serial = arn:aws:iam::111122223333:mfa/registry-operator
   region = us-east-1
   output = json

   [profile registry-deploy]
   role_arn = arn:aws:iam::111122223333:role/RegistryPortalDeployer
   source_profile = registry-operator
   mfa_serial = arn:aws:iam::111122223333:mfa/registry-operator
   region = us-east-1
   output = json

   [profile registry-break-glass]
   role_arn = arn:aws:iam::111122223333:role/RegistryPortalBreakGlass
   source_profile = registry-operator
   mfa_serial = arn:aws:iam::111122223333:mfa/registry-operator
   region = us-east-1
   output = json
   ```

5. 各プロファイルで次のコマンドを実行します。MFA コードを求められたら入力し、`Arn` に `assumed-role/<ロール名>/` が含まれることを確認します。

   ```bash
   aws sts get-caller-identity --profile registry-read
   ```

`registry-operator` 自体は `sts:AssumeRole` しか許可されていないため、`--profile registry-operator` で直接 AWS を操作することはできません。

[アクセスキーの管理](https://docs.aws.amazon.com/ja_jp/IAM/latest/UserGuide/id_credentials_access-keys.html) · [AWS CLI のロール用プロファイルと MFA](https://docs.aws.amazon.com/ja_jp/cli/latest/userguide/cli-configure-role.html)

## 10. Issue #2 に記録する

作業記録に次を残します。パスワード、アクセスキー、MFA のシークレットは記録しません。

- [ ] IAM ユーザー `registry-operator` の ARN と、MFA を設定済みであること。
- [ ] `RegistryPortalReadOnly`、`RegistryPortalDeployer`、`RegistryPortalBreakGlass` の ARN。
- [ ] 手順 8 のシミュレーション結果（すべての書き込みが `explicitDeny`）。
- [ ] break-glass ロールの担当者（本人）と、利用時の手順の保管先。

Issue #2 の受け入れ条件は「IAM Identity Center に permission set `RegistryPortalDeployer` を作成した」です。個人アカウントでは同名の IAM ロールで代替するため、[親 Issue #1](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/1) に次の内容をコメントします。

- **想定:** IAM Identity Center の permission set `RegistryPortalDeployer`。
- **確認結果:** IAM Identity Center は使用していない。同じ拒否設定を持つ IAM ロール `RegistryPortalDeployer` を作成し、IAM ユーザーから MFA 付きで引き受ける構成にした。
- **後続作業への影響:** デプロイ担当者は SSO プロファイルの代わりに、IAM ユーザーからロールを引き受ける AWS CLI プロファイル（`registry-deploy`）を使う。設計書と ADR-0004 の「permission set」の記述を、この構成に合わせる必要があるか判断が必要。
