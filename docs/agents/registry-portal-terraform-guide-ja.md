# Registry ポータルの Terraform ガイド（全体設計・既存資源の流用・初期設定・実行コマンド）

Registry ポータル（MVP1）を dev 環境に配信するための Terraform について、全体の構成、既存の AWS 資源の扱い、初回の準備、日常の実行コマンドをまとめます。設計の根拠は [MVP1 設計書](../../plan/aws-agent-registry-mvp1-design-ja.md)、[ADR-0003](../adr/0003-public-https-without-waf-and-single-registry.md)、[ADR-0004](../adr/0004-no-aws-deployment-from-ci.md) です。

**対象環境は、IAM Identity Center のない個人 AWS アカウント 1 つ**です。デプロイには、[個人アカウント向けの IAM 手順](registry-portal-iam-setup-personal-account-ja.md)で作る IAM ロール `RegistryPortalDeployer` を使います。

> [!IMPORTANT]
> **Issue #5・#49 の統合実装の状態**
>
> - bootstrap と `scripts/deploy.sh` は、MFA 付き IAM ロール `RegistryPortalDeployer` を使う個人アカウントに対応しています。Terraform には `credential_process` のプロファイルを渡します（3.4、4.2）。
> - 本体と追加スタック（`management`、`decision`、`notification_events`）は別 state で運用します。本体の output を各追加スタックの tfvars に手動で転記します（[ADR-0013](../adr/0013-separate-portal-addon-states.md)）。
> - dev への AWS apply と手動受入は未実施です。実装で解消した点と運用時の確認事項は「[7. 統合実装の状態と確認事項](#7-統合実装の状態と確認事項)」にまとめています。

## 目次

1. [全体設計](#1-全体設計)
2. [既存資源の流用](#2-既存資源の流用)
3. [初期設定（初回だけ行う作業）](#3-初期設定初回だけ行う作業)
4. [配信の手順](#4-配信の手順)
5. [実行コマンド一覧](#5-実行コマンド一覧)
6. [トラブルシューティング](#6-トラブルシューティング)
7. [統合実装の状態と確認事項](#7-統合実装の状態と確認事項)

## 1. 全体設計

### 1.1 設計方針

| 方針 | 内容 | 根拠 |
| --- | --- | --- |
| 既存資源は所有しない | 既存の Registry と Cognito User Pool の本体は Terraform の resource にしない。ARN や ID を変数で受け取り、借りるだけにする | 設計書「デプロイと CI」、ADR-0003 |
| CI は反映しない | GitHub Actions では `terraform fmt -check` と `validate` だけを行う。AWS の認証情報と OIDC ロールは持たせない | ADR-0004 |
| 人が手元で反映する | オペレーターが MFA 付きで IAM ロール `RegistryPortalDeployer` を引き受け、その短期認証情報で反映する。長期アクセスキーで直接操作しない | 設計書「デプロイと CI」、個人アカウント向け IAM 手順 |
| plan をガードに通す | 保存した plan を JSON にして `scripts/check_plan.py` で検査し、目視で確認してから、その plan だけを apply する | Issue #5 |
| 最小権限 | Lambda の実行ロールは対象 Registry の ARN と必要な action だけに絞る。Lambda ごとに実行ロールを分ける | 設計書「最小インフラとセキュリティ」 |
| 削除事故を防ぐ | state バケットと補助テーブルには `prevent_destroy` を付け、補助テーブルには削除保護も設定する。dev の撤去のときだけ保護を一時的に外す（5.4） | Issue #5、ADR-0014 |

### 1.2 IAM Identity Center を使わない理由

設計書は、Identity Center の permission set `RegistryPortalDeployer` を前提にしています。個人アカウントでは、これを同じ名前の IAM ロールで代替します。

- Identity Center の**アカウントインスタンス**は permission set に対応していないため、AWS アカウントへのアクセスには使えません（[AWS ドキュメント](https://docs.aws.amazon.com/singlesignon/latest/userguide/account-instances-identity-center.html)）。
- permission set を使うには**組織インスタンス**が必要です。そのためには AWS Organizations を作成し、個人アカウントを管理アカウントにする必要があります。MVP1 の dev のためにそこまでの手間をかけないので、本書では採用しません。

### 1.3 3 つの層

構成は「既存資源」「基盤スタック」「追加スタック」の 3 層に分かれます。

```mermaid
flowchart TB
  subgraph existing["既存資源・手動で作るもの（Terraform では所有しない）"]
    REG["AWS Agent Registry（us-east-1）"]
    POOL["Cognito User Pool とドメイン"]
    M2M["発見用 M2M client と secret（Secrets Manager）"]
    IAM["IAM ユーザー registry-operator と<br/>ロール RegistryPortalDeployer ほか"]
    BUS["EventBridge の default バス"]
  end

  subgraph base["基盤スタック"]
    STATE["bootstrap<br/>state 用 S3 バケット"]
    MAIN["本体スタック<br/>SPA・REST API・管理 Lambda・補助テーブル・Cognito の子リソース"]
  end

  subgraph addon["追加スタック（別 state）"]
    MGMT["management<br/>管理ロールへの権限追加・下書きなどの管理 route"]
    DEC["decision<br/>決裁 Lambda・決裁 route"]
    EVT["notification_events<br/>イベント処理 Lambda・EventBridge ルール・DLQ"]
  end

  STATE --> MAIN
  IAM -. 引き受けて実行 .-> MAIN
  MAIN --> MGMT
  MAIN --> DEC
  MAIN --> EVT
  POOL -. 子リソースを追加 .-> MAIN
  REG -. ARN で参照 .-> MAIN
  M2M -. ARN で参照 .-> MAIN
  BUS -. ルールを追加 .-> EVT
```

AWS 上の構成図は [`docs/diagrams/agent-registry-mvp1-aws-architecture-ja.drawio`](../diagrams/agent-registry-mvp1-aws-architecture-ja.drawio) を参照してください。

### 1.4 スタック一覧

パスは `applications/registry_portal/` からの相対パスです。state の key は、個人アカウントでは `registry-portal/dev/` の下にそろえます。

| スタック | ディレクトリ | 主に作るもの | state の key | 状態 |
| --- | --- | --- | --- | --- |
| bootstrap | `infrastructure/bootstrap/` | state 用 S3 バケット。Identity Center の permission set と割り当ては任意 | ローカルの `terraform.tfstate` | 個人アカウント対応済み。state は暗号化した場所で別途保管する（3.5） |
| 本体 | `infrastructure/`（直下） | SPA 用 S3 と CloudFront（OAC、CSP、viewer request function）、REST API・Cognito User Pools authorizer・stage、管理 Lambda と実行ロール、補助 DynamoDB、ロググループ、Cognito のリソースサーバー・人間用 client・グループ | `registry-portal/dev/terraform.tfstate` | 統合実装済み。追加スタック向け output を提供する |
| management | `infrastructure/management/` | 管理 Lambda のロールへの下書き用 inline policy、管理 handler の追加 route（下書き・申請・操作照会、管理レコード、接続情報、履歴、廃止依頼、通知）と呼び出し許可 | `registry-portal/dev/management.tfstate` | 別 state で運用。本体の output を転記する |
| decision | `infrastructure/decision/` | 決裁 Lambda、専用の実行ロール、ロググループ、決裁 route（審査、承認・却下、公開停止、廃止、回復など）と呼び出し許可 | `registry-portal/dev/decision.tfstate` | 別 state で運用。顧客管理 KMS キーに対応済み（#49） |
| notification_events | `infrastructure/notification_events/` | イベント処理 Lambda、専用の実行ロール、ロググループ、DLQ（SQS）、EventBridge ルールとターゲット、再試行の設定 | `registry-portal/dev/notification-events.tfstate` | 別 state で運用。CI の検証対象に追加済み |

どのスタックも `RegistryPortalDeployer` で実行します。

### 1.5 モジュール

| モジュール | 呼び出し元 | 内容 |
| --- | --- | --- |
| `modules/management_drafts` | `management` | 既存の管理ロールに、Registry レコードの作成・更新・取得・一覧・申請と、補助テーブルの GetItem / PutItem / DeleteItem / Query / ConditionCheckItem を追加する（`draft-policy.json.tftpl`） |
| `modules/api_routes` | 本体・管理・決裁 | 共通の経路表（`routes.json`）の 1 グループ（`main`・`management`・`decision`）について、メソッドと Lambda 統合を作り、そのメソッドと path に限った `lambda:InvokeFunction` を許可する |
| `modules/decision` | `decision` | 決裁 Lambda（`registry_portal.handlers.curation.handler`）。権限は `UpdateRegistryRecordStatus` と読み取り、発見用 secret の取得、補助テーブルの操作（`decision-policy.json.tftpl`） |
| `modules/notification_events` | `notification_events` | イベント処理 Lambda（`registry_portal.handlers.events.handler`）。Registry の読み取りと、通知用のパーティションキーに限った PutItem（`events-policy.json.tftpl`） |

Lambda はどれも Python 3.13、arm64、タイムアウト 30 秒、メモリ 256 MB です。ロググループの保存期間は既定で 365 日です。各スタックの変数 `log_retention_in_days` で変えられ、dev では 14 日にします。

### 1.6 IAM の主体と職責

| 主体 | 作り方 | 用途と権限 |
| --- | --- | --- |
| IAM ユーザー `registry-operator` | 手動（IAM 手順の 1） | サインイン用。MFA 必須。権限は下の 3ロールの `sts:AssumeRole` だけ |
| IAM ロール `RegistryPortalReadOnly` | 手動（IAM 手順の 3） | Issue #2 の読み取り確認 |
| IAM ロール `RegistryPortalDeployer` | 手動（IAM 手順の 4） | Terraform の実行。`AdministratorAccess` に加えて、Registry への書き込みと、保護対象の IAM 主体の変更を明示的に Deny する |
| IAM ロール `RegistryPortalBreakGlass` | 手動（IAM 手順の 5） | 緊急時の Registry レコードの直接変更 |
| 管理 Lambda の実行ロール | 本体と management | 読み取り、`CreateRegistryRecord`、`UpdateRegistryRecord`、`SubmitRegistryRecordForApproval`。決裁（`UpdateRegistryRecordStatus`）は持たない |
| 決裁 Lambda の実行ロール | decision | 読み取り、`UpdateRegistryRecordStatus`。descriptor の作成・更新と申請は持たない |
| イベント処理 Lambda の実行ロール | notification_events | `GetRegistryRecord` だけ |

Lambda の実行ロールには、Registry 本体の作成・削除・設定変更、レコードの物理削除、`iam:PassRole` を付けません。

発見用 M2M の secret（`secretsmanager:GetSecretValue`。顧客管理キーの場合は Secrets Manager 経由の `kms:Decrypt` も）を取得できるのは、管理 Lambda と決裁 Lambda の実行ロールだけです。決裁ロールは、Admin の既存レコードの取り込み（#16）で公開版を発見 API から取得するために使います。Issue #5 のコメントにある「管理 Lambda 以外のロールが secret を取得できないこと」は、この理由で決裁ロールを例外とし、イベント処理ロールなどその他の主体には付けません（7 章の項目 12）。

`RegistryPortalDeployer` は `AdministratorAccess` を持つため、permission set よりも権限が広くなります。権限の境界は、plan のガード（`check_plan.py`）と apply 前の目視確認で守ります（IAM 手順の 4の注記）。

## 2. 既存資源の流用

### 2.1 流用する資源と渡し方

| 既存資源 | Terraform での扱い | 渡す変数 | 確認方法 |
| --- | --- | --- | --- |
| AWS Agent Registry | 所有しない。ARN を参照する。`check_plan.py` が create / update / delete / 置換を検出したら中止する | `registry_arn` | [既存 Registry の確認手順](registry-portal-existing-registry-check-ja.md) の 1・2 |
| Cognito User Pool | 本体とドメインは所有しない。子リソース（リソースサーバー `registry-portal`、人間用 client、グループ）だけを本体スタックで追加する | `cognito_user_pool_id`、`cognito_domain`、`identity_providers` | 同上の 4・7 |
| 発見用 M2M client | 所有しない。client ID とトークンエンドポイントを Lambda の環境変数に渡す | `discovery_client_id`、`discovery_token_url`、`discovery_scope` | 同上の 4・7 |
| 発見用 M2M の secret | 所有しない。完全な ARN だけを渡し、secret の値は Terraform に渡さない | `discovery_client_secret_arn` | 同上の 4「secret の保管先」 |
| secret の KMS キー | 顧客管理キーの場合だけ ARN を渡す。本体スタックの管理 Lambda と decision スタックの決裁 Lambda に、Secrets Manager 経由で、対象 secret の暗号化コンテキストに限った `kms:Decrypt` を付ける。両方のスタックに同じ値を渡す | `discovery_kms_key_arn`（AWS 管理キーなら `null`） | 同上の 4・7 |
| IAM ユーザーと 3 つのロール | 所有しない。手動で作る | なし | [個人アカウント向けの IAM 手順](registry-portal-iam-setup-personal-account-ja.md) |
| state 用 S3 バケット | bootstrap が所有する。本体と追加スタックは backend として借りる（3.5） | `backend.hcl` の `bucket` | 3.5 |
| EventBridge の default バス | 所有しない。ルールを 1 本追加する | なし（`event_bus_name = "default"` で固定） | — |
| CloudTrail | Terraform では扱わない。個人アカウントに組織の trail はないので、イベント履歴（90 日）を使うか、必要なら trail を手動で作る | なし | — |

### 2.2 同じ名前の資源がすでにある場合

同じ資源を複数の state で管理しないでください。すでに資源がある場合は `terraform import` で取り込み、plan に作り直しがないことを確認します。

| 既存の可能性があるもの | 取り込み先のアドレス | import ID の形式 |
| --- | --- | --- |
| Cognito グループ（`registry-publisher` など） | `aws_cognito_user_group.business["registry-publisher"]` | `<user_pool_id>/<グループ名>` |
| チームグループ（`team-*`） | `aws_cognito_user_group.team["<チーム名>"]` | `<user_pool_id>/team-<チーム名>` |
| リソースサーバー `registry-portal` | `aws_cognito_resource_server.portal` | `<user_pool_id>\|registry-portal` |

import ID の形式は AWS provider のバージョンで変わることがあります。実行前に [Terraform Registry の各 resource の Import 節](https://registry.terraform.io/providers/hashicorp/aws/6.71.0/docs) で確認してください。

本体スタックは `lambda_zip_path` の zip のハッシュを読むため、import の前にも zip が必要です。4.2 の `init` を済ませてから、`applications/registry_portal` で実行します。

```bash
# 例: 本体スタックに既存のグループを取り込む
cd applications/registry_portal
python3 scripts/build_lambda.py --output "$PWD/build/lambda.zip"
USER_POOL_ID='dev.tfvars の cognito_user_pool_id'
AWS_PROFILE=registry-deploy-tf terraform -chdir=infrastructure import -var-file=dev.tfvars \
  -var="lambda_zip_path=$PWD/build/lambda.zip" \
  'aws_cognito_user_group.business["registry-publisher"]' "$USER_POOL_ID/registry-publisher"
rm "$PWD/build/lambda.zip"
```

### 2.3 作らないもの

- ポータル利用者、発見用 M2M、CI 用の IAM ユーザーとロール。認証には Cognito を使います。
- GitHub Actions から AWS に反映するためのロール（ADR-0004）。
- IAM Identity Center、AWS Organizations。
- VPC、NAT Gateway、ALB、ECS、WAF、独自ドメイン、Route 53（ADR-0003）。
- Lambda の Function URL。

## 3. 初期設定（初回だけ行う作業）

### 3.1 必要なツール

| ツール | バージョン | 備考 |
| --- | --- | --- |
| Terraform | 1.10 以上、2.0 未満。mock テストと CI は 1.17.1 | S3 ネイティブのロック（`use_lockfile`）に 1.10 以上が必要。mock テストの後片付けには、1.14 以降の [`prevent_destroy` をテスト終了時に無視する動作](https://github.com/hashicorp/terraform/blob/v1.14.0/CHANGELOG.md)を使う。実スタックの削除保護は維持する |
| AWS provider | すべてのルートスタックで `= 6.71.0` | `.terraform.lock.hcl` は `.gitignore` の対象 |
| Python | 3.13 と pip | Lambda の zip を作るのに使う |
| Node.js | 24 と npm | SPA のビルドに使う |
| AWS CLI | v2（`aws configure export-credentials` が使えるもの） | MFA 付きのロールを Terraform に渡すのに使う（3.4） |

### 3.2 事前に確定する値

Issue #2 の読み取り確認（[既存 Registry の確認手順](registry-portal-existing-registry-check-ja.md)）で、次の値を確定しておきます。確認手順の手順 7 で、これらの値をまとめ、ポータルが作るグループ・リソースサーバー・IAM ロールと同じ名前のものがないかも確認します。

| 値 | 使う変数 | 使うスタック |
| --- | --- | --- |
| AWS アカウント ID | `aws_account_id` | 本体 |
| Registry ARN | `registry_arn` | 本体、すべての追加スタック |
| Cognito User Pool ID | `cognito_user_pool_id` | 本体、decision |
| Cognito ドメイン（`https://...amazoncognito.com`、末尾の `/` なし） | `cognito_domain` | 本体 |
| 人間用 IdP 名（User Pool のユーザーでログインするなら `COGNITO`） | `identity_providers` | 本体 |
| 所属チーム名（`team-` は付けない） | `team_names` | 本体 |
| 発見用 M2M のトークン URL、client ID、scope | `discovery_token_url`、`discovery_client_id`、`discovery_scope` | 本体、decision |
| 発見用 M2M secret の ARN | `discovery_client_secret_arn` | 本体、decision |
| 発見用 M2M secret の KMS キーの ARN（顧客管理キーの場合だけ） | `discovery_kms_key_arn` | 本体、decision |
| state 用バケット名と SPA 用バケット名（どちらもグローバルに一意） | `backend.hcl` の `bucket`、`spa_bucket_name` | すべて、本体 |

### 3.3 IAM ユーザーとロールを作る

[個人アカウント向けの IAM 手順](registry-portal-iam-setup-personal-account-ja.md)の 1〜8を済ませます。9の「ローカル端末を使う」で、`registry-operator` のプロファイルと、ロールごとのプロファイル（`registry-read`、`registry-deploy`、`registry-break-glass`）を作っておきます。

### 3.4 Terraform 用のプロファイルを作る

Terraform の AWS provider は、MFA コードの入力を求めることができません。`mfa_serial` 付きのプロファイル（`registry-deploy`）を Terraform にそのまま渡すと、`AssumeRoleTokenProviderNotSetError` で失敗します（[Classmethod の解説](https://dev.classmethod.jp/articles/terraform-mfa-assumerole-export-credentials/)）。

そこで、AWS CLI が取得してキャッシュした `RegistryPortalDeployer` の短期認証情報を、`credential_process` で Terraform に渡すプロファイルを追加します。`~/.aws/config` に次を追記します。

```ini
[profile registry-deploy-tf]
credential_process = aws configure export-credentials --profile registry-deploy --format process
region = us-east-1
```

使う前に、AWS CLI で一度ロールを引き受けて MFA コードを入力し、短期認証情報をキャッシュさせます。キャッシュの有効期間は、ロールの最大セッション時間（既定は 1 時間）です。期限が切れたら、このコマンドをもう一度実行します。

```bash
aws sts get-caller-identity --profile registry-deploy      # MFA コードを入力する
aws sts get-caller-identity --profile registry-deploy-tf   # MFA を求められず、同じ Arn が表示されることを確認する
# Arn: arn:aws:sts::<アカウント ID>:assumed-role/RegistryPortalDeployer/...
```

`credential_process` のコマンドは MFA コードを対話的に入力できない場合があります。2 行目で MFA を求められたり失敗したりする場合は、1 行目を実行し直してキャッシュを作ってください。

環境変数 `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_SESSION_TOKEN` は設定しません。設定されているとプロファイルより優先されます。

### 3.5 state 用 S3 バケットを作る

bootstrap を使って state 用バケットを作ります。3.4 の MFA キャッシュと Terraform 用プロファイルを先に用意してください。

```bash
cd applications/registry_portal
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
aws sts get-caller-identity --profile registry-deploy
export AWS_PROFILE=registry-deploy-tf
cp infrastructure/bootstrap/terraform.tfvars.example infrastructure/bootstrap/terraform.tfvars
```

`terraform.tfvars` にアカウント ID、state バケット名、SPA バケット名、既存 User Pool ID を設定します。`name_prefix` は本体とそろえます。個人アカウントでは次の値を設定し、`identity_center_instance_arn` と `deployer_group_id` は既定の `null` のままにします。

```hcl
deployer_principal_arn   = "arn:aws:iam::<アカウント ID>:role/RegistryPortalDeployer"
bootstrap_admin_role_arn = "arn:aws:iam::<アカウント ID>:role/RegistryPortalDeployer"
```

`deployer_principal_arn` は state とロックの内容を読み書きできる主体です。`aws:PrincipalArn` と照合する IAM ロール ARN を渡し、STS セッション ARN は使いません。`bootstrap_admin_role_arn` は bootstrap を実行する既存 IAM ロールです。この変数は `HeadBucket` に必要なバケット一覧の拒否だけを免除し、state とロックの内容へのアクセス権は追加しません。個人アカウントで Deployer 自身が bootstrap を実行する場合は、上記のように両方を同じ ARN にします。

設定後、保存した plan を確認して反映します。

```bash
terraform -chdir=infrastructure/bootstrap init
terraform -chdir=infrastructure/bootstrap validate
terraform -chdir=infrastructure/bootstrap plan -out=bootstrap.tfplan
terraform -chdir=infrastructure/bootstrap show bootstrap.tfplan
terraform -chdir=infrastructure/bootstrap apply bootstrap.tfplan
rm infrastructure/bootstrap/bootstrap.tfplan
```

bootstrap はバージョニング、AES256 暗号化、公開アクセス禁止、TLS 必須のバケットポリシーを設定します。バケットには `prevent_destroy` を付けています。ライフサイクル規則は直近 10 本の旧バージョンを保持し、それより古い旧バージョンを 90 日で期限切れにします。未完了のマルチパートアップロードは 1 日で中止します。bootstrap 自身の state はローカルに作るため、初回管理者が暗号化した場所で保管してください。本体や追加スタックの state と混ぜません。

月額予算を作る場合は、bootstrap の設定で `budget_limit_usd = 5` を目安にし、`TF_VAR_budget_notification_emails` に通知先の JSON 配列を設定して plan と apply を実行します。メールアドレスは tfvars に書かず、コミットしません。上限と通知先を両方指定したときだけ予算を作り、実績が上限の 80%、予測が 100% を超えたときに通知します。

Identity Center を使う環境では、`identity_center_instance_arn` と `deployer_group_id` を両方指定すると permission set と割り当ても作ります。`deployer_principal_arn` には既存の SSO ロール ARN パターンを指定します。既存 permission set の import を含む手順は [README の手動配信](../../applications/registry_portal/README.md#dev-に手動配信する)を参照してください。

### 3.6 本体スタックの設定ファイルを作る

```bash
cd applications/registry_portal
cp infrastructure/backend.hcl.example infrastructure/backend.hcl
cp infrastructure/terraform.tfvars.example infrastructure/dev.tfvars
```

- `backend.hcl` の `bucket` には、3.5 で作ったバケット名を設定します。key は `registry-portal/dev/terraform.tfstate` のままにします。
- `dev.tfvars` には、アカウント ID、SPA バケット名、`name_prefix`（既定は `registry-portal-dev`）などを設定します。secret の値は書きません。

| 変数 | dev の推奨値 | 設定先 |
| --- | --- | --- |
| `log_retention_in_days` | `14`（既定は 365 日） | 本体・決裁・イベント処理 |
| `spa_noncurrent_version_expiration_days` | `7`（既定は `null`、旧バージョンを保持） | 本体 |
| `local_dev_origins` | `["http://localhost:5173"]`（既定は `[]`） | 本体 |
| `allowed_source_cidrs` | 許可する IPv4 CIDR を 1 件以上、JSON 配列で指定 | 本体の環境変数 `TF_VAR_allowed_source_cidrs` |
| `lambda_reserved_concurrency` | 本体と決裁は `5`、イベント処理は `2`〜`5`（既定は `null`、予約なし） | 本体・決裁・イベント処理 |

予約同時実行数を設定する前に、対象リージョンで `aws lambda get-account-settings` を実行して `AccountLimit.ConcurrentExecutions` と既存の予約を確認します。予約後も未予約の枠を 100 残せる場合に設定してください。イベント処理では、上限に達した場合の再試行とデッドレターキューへの転送を考慮してください。

Identity Center の deployer で SPA のライフサイクル設定を初めて反映する前に、管理者が bootstrap を再適用して deployer に `s3:PutLifecycleConfiguration` を付与します。

`dev.tfvars` と `backend.hcl` は `.gitignore` の対象です。コミット前に `git status` でも確認してください。

### 3.7 Cognito User Pool の設定を確認する

既存の Cognito User Pool は Terraform の管理対象外です（2.1）。本体スタックは子リソースを追加するだけで、自己サインアップ、MFA、料金プラン（feature plan）は変更しません。そのため、配信の前に次の 3 点を手で確認します。満たさない項目は差分として記録し、User Pool の設定を変える場合はポータルの配信とは別に行います。

| 確認項目 | 期待する値（`describe-user-pool` の項目） | 理由 |
| --- | --- | --- |
| 自己サインアップが無効 | `AdminCreateUserConfig.AllowAdminCreateUserOnly` が `true` | `false` のときは、誰でも `SignUp` でユーザーを作れます。設計では、ユーザーは Admin が作ります（4.1 の 7） |
| ポータルを使うユーザーに MFA が設定されている | `MfaConfiguration` が `ON`、または `OPTIONAL` で、ポータルを使う全ユーザーに MFA が設定されている | `OFF` では MFA を使えません。`OPTIONAL` では、MFA を設定していないユーザーがパスワードだけでログインできます |
| 料金プランが Lite か Essentials | `UserPoolTier` が `LITE` または `ESSENTIALS`（`PLUS` でない） | Plus は Essentials に脅威保護（`UserPoolAddOns.AdvancedSecurityMode`）などを加えたプランで、dev には不要です |

自己サインアップ、MFA、料金プランは、`registry-read` で確認できます（`RegistryPortalReadOnly` は `cognito-idp:Describe*` と `List*` を持ちます）。

```bash
COGNITO_REGION=us-east-1
USER_POOL_ID='dev.tfvars の cognito_user_pool_id'

aws cognito-idp describe-user-pool --profile registry-read --region "$COGNITO_REGION" \
  --user-pool-id "$USER_POOL_ID" \
  --query 'UserPool.{adminCreateOnly:AdminCreateUserConfig.AllowAdminCreateUserOnly,mfa:MfaConfiguration,tier:UserPoolTier,threatProtection:UserPoolAddOns.AdvancedSecurityMode,domain:Domain,customDomain:CustomDomain}' \
  --output json
```

- `adminCreateOnly` が `true` であることを確認します。
- `mfa` が `ON` なら、全ユーザーに MFA が必須です。`OPTIONAL` なら、下の手順でポータル利用者ごとに確認します。既存の User Pool でも `SetUserPoolMfaConfig` で `ON` に設定できます。 [SetUserPoolMfaConfig の API リファレンス](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_SetUserPoolMfaConfig.html)
- `tier` が `LITE` か `ESSENTIALS` であることを確認します。`PLUS` の場合は、`threatProtection`（脅威保護）が `OFF` 以外で使われていないかもあわせて記録します。プランを下げるには、上位のプランにしかない機能を先に止める必要があります（[Turning off features to change feature plans](https://docs.aws.amazon.com/cognito/latest/developerguide/feature-plans-deactivate.html)）。

Lite と Essentials のどちらで足りるかは、ログイン画面の種類で決まります。managed login は Essentials 以上が必要で、Lite で使えるのは classic hosted UI だけです。ドメインのブランディングは次のコマンドで確認します（`domain` の値。`customDomain` があればそちら）。

```bash
COGNITO_DOMAIN_NAME='上の domain または customDomain の値'
aws cognito-idp describe-user-pool-domain --profile registry-read --region "$COGNITO_REGION" \
  --domain "$COGNITO_DOMAIN_NAME" \
  --query 'DomainDescription.{managedLoginVersion:ManagedLoginVersion,status:Status}' --output json
```

`managedLoginVersion` が `2` なら managed login で、Essentials 以上が必要です。`1` なら classic hosted UI で、Lite でも使えます。ただし、ポータルが classic hosted UI でログインできるかは未確認です（README の受入確認は managed login を前提にしています）。料金は [Amazon Cognito の料金](https://aws.amazon.com/cognito/pricing/)で確認してください。

`mfa` が `OPTIONAL` の場合は、Cognito でユーザーを作った後（4.1 の 7）に、ポータルを使うユーザーの MFA を確認します。`AdminGetUser` は照会したユーザーを課金用の月間アクティブユーザー（MAU）に算入するため、対象のユーザー名を 1 人ずつ指定してください。照会には `registry-deploy` を使います。外部 IdP のユーザーは、IdP 側の MFA 設定を確認してください。 [Amazon Cognito の MAU 集計ルール](https://docs.aws.amazon.com/cognito/latest/developerguide/quotas.html)

```bash
PORTAL_USERNAME='確認対象のポータル利用者の Cognito ユーザー名'
aws cognito-idp admin-get-user --profile registry-deploy --region "$COGNITO_REGION" \
  --user-pool-id "$USER_POOL_ID" --username "$PORTAL_USERNAME" \
  --query '{user:Username,mfa:UserMFASettingList,preferred:PreferredMfaSetting}' --output json
```

`mfa` が `null` または空の配列なら、ポータルを使い始める前に、そのユーザーに MFA を設定してもらってください。

出典（AWS の公式文書）：

- [DescribeUserPool](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_DescribeUserPool.html)（出力は `UserPool` の下に入る）
- [AdminCreateUserConfigType](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AdminCreateUserConfigType.html)（`AllowAdminCreateUserOnly`）
- [UserPoolType](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_UserPoolType.html)（`MfaConfiguration` の `OFF | ON | OPTIONAL`、`UserPoolTier` の `LITE | ESSENTIALS | PLUS`）
- [UserPoolAddOnsType](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_UserPoolAddOnsType.html)（`AdvancedSecurityMode`）
- [DomainDescriptionType](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_DomainDescriptionType.html)（`ManagedLoginVersion`。managed login は Lite 以外のプランが必要）
- [AdminGetUser](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AdminGetUser.html)（`UserMFASettingList`、`PreferredMfaSetting`）
- [User pool feature plans](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-sign-in-feature-plans.html)、[Essentials plan features](https://docs.aws.amazon.com/cognito/latest/developerguide/feature-plans-features-essentials.html)

## 4. 配信の手順

### 4.1 適用の順序

| 順序 | 作業 | プロファイル |
| --- | --- | --- |
| 0 | Issue #2 の読み取り確認で、既存資源の値を確定する。Cognito User Pool の設定を確認する（3.7） | `registry-read` |
| 1 | IAM ユーザーとロールを作る（3.3） | ルートユーザーまたは管理者 |
| 2 | bootstrap で state 用バケットを作る（3.5） | `registry-deploy-tf` |
| 3 | 本体スタックを配信する（4.2） | `registry-deploy-tf` と `registry-deploy` |
| 4 | `management`（下書き権限と管理 route） | `registry-deploy-tf` |
| 5 | `decision`（決裁 Lambda と route） | `registry-deploy-tf` |
| 6 | `notification_events`（通知） | `registry-deploy-tf` |
| 7 | Cognito でユーザーを作り、グループに割り当てる。ユーザーごとの MFA を確認する（3.7） | コンソール（`registry-deploy`） |
| 8 | dev で受入確認を行う（Issue #5 の README のチェックリスト） | — |

4〜6 は順に実行します。REST API を更新する管理・決裁の配信は同時に実行しないでください。本体スタックより後に実行します。各追加スタックは本体スタックの資源を借ります。本体の output を各スタックの tfvars に転記してください。

### 4.2 本体スタックを配信する

個人アカウントでは、`role_arn` と `mfa_serial` を設定した `registry-deploy` を `AWS_PROFILE` に指定します。3.4 の `registry-deploy-tf` は `credential_process` で同じセッションを Terraform に渡します。Terraform 用の名前は既定で `<AWS_PROFILE>-tf` です。別名の場合は `TERRAFORM_AWS_PROFILE` を指定してください。

```bash
cd applications/registry_portal
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
aws sts get-caller-identity --profile registry-deploy
# 文書用の例。実際に許可する IPv4 CIDR に置き換える。
export TF_VAR_allowed_source_cidrs='["203.0.113.10/32"]'
AWS_PROFILE=registry-deploy scripts/deploy.sh infrastructure/backend.hcl infrastructure/dev.tfvars
```

スクリプトは `assumed-role/RegistryPortalDeployer/*` の主体と、Terraform 用プロファイルが同じセッションを返すことを確認します。SDK 同梱 ZIP を生成し、backend の暗号化と `use_lockfile=true` を強制して初期化します。保存した plan を `check_plan.py` に通した後、目視確認のために表示します。

既存 Registry の作成・変更・削除がなく、IAM の対象 ARN と action が意図どおりであることを確認してください。反映する場合は `apply` と入力します。スクリプトは保存した同じ plan だけを apply します。ガード違反やコマンド失敗、中止があれば、後続の操作は実行しません。

apply 後は `frontend_config` で SPA をビルドし、アセット、非キャッシュの `index.html` の順に S3 に配置して CloudFront のキャッシュを無効化します。一時 ZIP と plan は終了時に削除します。CI 内からの実行と環境変数のアクセスキーは拒否します。

`frontend_config` には、`VITE_COGNITO_DOMAIN`、`VITE_COGNITO_CLIENT_ID`、`VITE_REDIRECT_URI`、`VITE_API_BASE_URL`、`VITE_COGNITO_SCOPES` が入ります。`VITE_API_BASE_URL` は末尾にステージの `/v1` を含みます。secret は含みません。

Identity Center を使う場合も、本体スタックには同じスクリプトを使います。SSO プロファイルでログインし、`AWSReservedSSO_RegistryPortalDeployer_*` の主体で実行してください（[README](../../applications/registry_portal/README.md#dev-に手動配信する)）。bootstrap の permission set は本体の state key と管理 Lambda 向けの権限に限ります。追加 3 スタックの state key や決裁・イベント処理の資源には対応していないため、`deploy.sh` は SSO プロファイルと `--stack` の組み合わせを Terraform の実行前に拒否します。追加スタックは、個人アカウントの MFA 付き IAM ロール経路でだけ配信します（dev は個人アカウントだけで運用するため、permission set は追加スタック向けに広げません）。

#### 接続元 IP を更新する

Cognito のログイン画面と CloudFront の SPA は表示できても、REST API は接続元 IPv4 が許可 CIDR の外にあると呼び出せません。ローカル SPA の接続元は `localhost` ではなく、ブラウザが AWS に接続するときの外向き IPv4 アドレスです。

1. 許可するアドレスを確認し、必要な CIDR を JSON 配列で `TF_VAR_allowed_source_cidrs` に設定します。IP は tfvars に記録せず、Git にコミットしません。
2. 4.2 の本体スタックの配信を実行します。表示された plan でリソースポリシーの変更を確認し、反映します。
3. 配信後、許可したネットワークから SPA にログインし、カタログを取得します。既存の許可元を入れ替える場合は、両方を一度許可し、新しい接続元で確認してから古い CIDR を外します。

締め出された場合は、配信権限のある AWS CLI プロファイルを使い、正しい CIDR を環境変数に設定して同じ本体スタックを配信し直します。REST API の接続元制限はポータルへの呼び出しに適用されます。Terraform や API Gateway の更新権限は、従来どおり IAM で管理します。

#### HTTP API から切り替える

1. 管理者が対象リージョンで `aws apigateway get-account --region us-east-1 --query cloudwatchRoleArn` を実行します。すでに有効なロールが設定されていれば、そのロールを使います。ロール設定がなければ bootstrap で `manage_api_gateway_account = true` を設定し、再適用します。この設定はリージョン内の API Gateway REST API で共有されます。既存ロールを使う場合も、Identity Center の deployer を使う環境では REST API の更新権限を反映するため、bootstrap を再適用します。
2. 本体を配信します。既存 HTTP API が REST API に置き換わるため、追加スタックを反映するまで、その経路は利用できません。
3. 本体の新しい output `rest_api` を 4.3 に沿って転記し、管理・決裁を順に配信します。ローカル SPA の設定も、新しい URL に `/v1` を含めて更新します。追加スタックは撤去せず、既存 state をそのまま使います。HTTP API と一緒に削除された route は、追加スタックの plan の refresh で state から外れます。旧 HTTP API 向けの Lambda 呼び出し許可は、REST API 向けの許可への置き換えとして plan に表示されます（[ADR-0015](../adr/0015-rest-api-with-source-ip-policy-and-script-deployment.md)）。
4. `local_dev_origins` を後から変更した場合も、本体の output `rest_api` を決裁スタックへ転記し直して反映します（`cors_allowed_origins` が変わるため）。

### 4.3 追加スタックに渡す値を集める

本体と追加スタックは別 state を維持します（[ADR-0013](../adr/0013-separate-portal-addon-states.md)）。各 `terraform.tfvars.example` を同ディレクトリの `terraform.tfvars` にコピーし、本体の output を手動で転記してください。例の名前や ID をそのまま使わないでください。

```bash
cd applications/registry_portal
AWS_PROFILE=registry-deploy-tf terraform -chdir=infrastructure output
```

| 変数 | 転記元 | 使うスタック |
| --- | --- | --- |
| `region` | `us-east-1` | すべて |
| `registry_arn` | 本体の `dev.tfvars` と同じ値 | すべて |
| `supplement_table_name` | 同名の本体 output | decision、notification_events |
| `supplement_table_arn` | 同名の本体 output | すべて |
| `management_role_name` | 同名の本体 output | management |
| `management_function_name` | 同名の本体 output | management |
| `rest_api` | 同名の本体 output をそのまま転記する（REST API の ID、ステージ名、Cognito User Pools authorizer の ID、route の authorizer が検証するカスタム scope `portal_api_scope`、CloudFront とローカル SPA の origin `cors_allowed_origins`） | management、decision |
| `cognito_user_pool_id` | 本体の `dev.tfvars` と同じ値 | decision |
| `human_client_id` | 同名の本体 output | decision |
| `discovery_token_url`、`discovery_client_id`、`discovery_client_secret_arn`、`discovery_scope` | 本体の `dev.tfvars` と同じ値。secret の ARN は末尾の 6 文字を含む完全な ARN にする（ワイルドカード不可） | decision |
| `discovery_kms_key_arn` | 本体の `dev.tfvars` と同じ値。顧客管理キーの場合だけ ARN を指定し、AWS 管理キーなら省略または `null` | decision |
| `lambda_zip_path` | 4.4 で作った共通 ZIP のパス | decision、notification_events |

`rest_api` は `terraform -chdir=infrastructure output rest_api` が表示するオブジェクトを、そのまま `rest_api = { ... }` として貼り付けます。management は `cors_allowed_origins` を使わないため変数の型に含めていません。それでも両スタックで同じ値を転記します（型にない属性は Terraform が捨てます）。`scripts/deploy.sh` は追加スタックの再デプロイ先（REST API の ID とステージ名）を、この値から読みます。

`cognito_issuer` の output は確認用です。decision のルートスタックは `region` と `cognito_user_pool_id` から issuer を組み立てるため、tfvars に `cognito_issuer` を追加しません。

`management_role_name` が本体の管理 Lambda の実行ロールであることを、output と plan で確認してください。ガードは policy の資源アドレスで役割を判定するため、別のロール名を誤って転記しても検出できません。

#49 の対応により、顧客管理キーの ARN を指定した場合だけ、決裁 Lambda に `kms:Decrypt` を付けます。権限は Secrets Manager 経由で、暗号化コンテキストの `SecretARN` が対象 secret と完全一致（`StringEquals`）する場合に限定します。条件の文は本体と共通の `modules/discovery_client` が作ります。本体と decision の両方に同じ ARN を渡してください。AWS 管理キーを使う場合は、両方で省略または `null` にします。

### 4.4 Lambda の zip を作る

`build_lambda.py` は `backend/src/registry_portal` 全体と固定版の依存パッケージを同梱します。決裁とイベント処理の Lambda は同じ ZIP を使い、handler だけを切り替えます。各スタックの例は `lambda_zip_path = "../../backend/build/lambda.zip"` を参照します。

```bash
cd applications/registry_portal
python3 scripts/build_lambda.py
```

出力先は既定で `backend/build/lambda.zip` です。`--output` で変更した場合は tfvars のパスも合わせてください。`backend/build/`、`infrastructure/backend.hcl` と追加スタックの `backend.hcl` は `.gitignore` の対象です。本体の `deploy.sh` は同じビルドスクリプトで、一時ディレクトリに共通 ZIP（`lambda.zip`）を生成します。

### 4.5 追加スタックを適用する

3 つの追加スタックも、本体と同じ `scripts/deploy.sh` に `--stack` を付けて反映します。4.3 で各 `terraform.tfvars` の設定を済ませてから進みます。各スタックの `backend.hcl.example` を `backend.hcl` にコピーし、`bucket` には本体の `infrastructure/backend.hcl` と同じ実際のバケット名を設定してください。key は次の表の値にします。`deploy.sh` は Terraform を実行する前に `backend.hcl` の `key` を確かめ、表と異なれば中止します（別のスタックの state を指定すると、plan にそのスタックの資源の削除が出るため）。`applications/registry_portal` で実行します。

| `--stack` | `key` |
| --- | --- |
| （省略。本体） | `registry-portal/dev/terraform.tfstate` |
| `management` | `registry-portal/dev/management.tfstate` |
| `decision` | `registry-portal/dev/decision.tfstate` |
| `notification_events` | `registry-portal/dev/notification-events.tfstate` |

```bash
cd applications/registry_portal
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
aws sts get-caller-identity --profile registry-deploy   # MFA コードを入力し、短期認証情報をキャッシュする
for STACK in management decision notification_events; do
  AWS_PROFILE=registry-deploy scripts/deploy.sh --stack "$STACK" \
    "infrastructure/$STACK/backend.hcl" "infrastructure/$STACK/terraform.tfvars" || break
done
```

スクリプトは本体と同じく、CI 内からの実行と環境変数の認証情報を拒否し、主体が `RegistryPortalDeployer` であること（個人アカウントでは Terraform 用プロファイルが同じセッションであること）を確認します。そのうえで一時ディレクトリに plan を保存し、`check_plan.py` に通して表示します。`apply` と入力したときだけ、その保存済み plan を反映します。`decision` と `notification_events` には、共通 ZIP（`lambda.zip`）を一時ディレクトリに作って `lambda_zip_path` に渡します（tfvars の値より優先されます）。追加スタックでは SPA のビルドと配信は行いません。1 つのスタックで中止・失敗したら、上の例は残りを実行せずに止まります。

`backend.hcl.example` を使う場合も、1.4 の表と同じ key を設定してください。`decision` と `notification_events` の IAM policy はロググループと DLQ の ARN を名前・アカウント ID・リージョンから組み立てるため、初回 plan でも確定してガードに通せます。ロググループは本体と共通の `modules/lambda_log_group` が作ります。適用済みの state は `moved` ブロックで引き継ぐため、モジュールへの移動でロググループが作り直されることはありません。plan に削除と作成が出た場合は `apply` しないでください。

## 5. 実行コマンド一覧

### 5.1 ローカルの検査（AWS の認証情報は不要）

```bash
cd applications/registry_portal

# 整形（全スタック）
terraform -chdir=infrastructure fmt -check -recursive

# 全スタックの検証（CI は notification_events も対象）
for d in . bootstrap management decision notification_events; do
  terraform -chdir=infrastructure/$d init -backend=false
  terraform -chdir=infrastructure/$d validate
done

# IAM ポリシーのテンプレートのテスト
(cd backend && pytest tests/test_decision_permissions.py)

# mock provider のテスト
terraform -chdir=infrastructure test
terraform -chdir=infrastructure/bootstrap test
python3 -m unittest discover -s infrastructure/tests -v
node --test infrastructure/tests/spa_rewrite.test.mjs
bash -n scripts/deploy.sh
```

### 5.2 AWS に反映するコマンド

| 目的 | コマンド |
| --- | --- |
| MFA を入力して短期認証情報をキャッシュする | `aws sts get-caller-identity --profile registry-deploy` |
| Terraform 用のプロファイルを確認する | `aws sts get-caller-identity --profile registry-deploy-tf` |
| state 用バケットを作る（初回だけ） | 3.5 のコマンド |
| 本体スタックを配信する | 4.2 のコマンド |
| 追加スタックを適用する | `AWS_PROFILE=registry-deploy scripts/deploy.sh --stack <management\|decision\|notification_events> infrastructure/<スタック>/backend.hcl infrastructure/<スタック>/terraform.tfvars`（本体と同じ主体の検査・`check_plan.py`・`apply` の入力確認を通す） |
| スタックを撤去する（追加スタック → 本体の順） | `AWS_PROFILE=registry-deploy scripts/deploy.sh --destroy [--stack <management\|decision\|notification_events>] <backend.hcl> <tfvars>`（保護の解除と順番は 5.4。確認ではスタック名を入力する） |
| 保存済み plan を手で検査する（各スタックのディレクトリから） | `python3 <check_plan.py のパス> --saved-plan <tfplan>` |

### 5.3 state の操作

`AWS_PROFILE=registry-deploy-tf` を設定して、各スタックのディレクトリで実行します。

| 目的 | コマンド |
| --- | --- |
| 管理対象の一覧 | `terraform state list` |
| 既存資源を取り込む | `terraform import -var-file=<tfvars> '<アドレス>' '<ID>'` |
| 管理対象から外す（AWS 上の資源は消さない） | `terraform state rm '<アドレス>'` |
| ロックが残ったとき | `terraform force-unlock <LOCK_ID>`（ほかに実行中の作業がないことを確認してから） |

現在は追加スタックの state を移動しません。後続 Issue で統合を決めた場合だけ、対象資源を移行先に import し、移行元で `terraform state rm` を実行します。移行中は apply を止め、同じ資源を複数の state から操作しないでください（[ADR-0013](../adr/0013-separate-portal-addon-states.md)）。

### 5.4 dev 環境を撤去する

dev 環境を作り直すときの撤去の手順です。方針と理由は [ADR-0014](../adr/0014-dev-teardown-protection-and-scope.md) にあります。

- 撤去の対象は、追加スタック（`management`・`decision`・`notification_events`）と本体スタックです。bootstrap と state バケットは対象外です。
- 撤去の順番は「追加スタック → 本体」です。追加スタックは本体の REST API・管理 Lambda・実行ロール・補助テーブルを借りて、route・Lambda permission・inline policy を足しています。本体を先に消すと、追加スタックの state に孤立したリソースが残ります。
- 撤去は `scripts/deploy.sh --destroy` で行います。配信と同じ主体の検査と `check_plan.py` を通し、`terraform plan -destroy` で保存した plan を表示します。撤去するスタック名を入力したときだけ、その plan を apply します（`y` や `apply` では進みません）。SPA の配信は行いません。
- `deploy.sh` は SSO プロファイルと `--stack` の組み合わせを拒否するため、個人アカウントの MFA 付き IAM ロール（`registry-deploy`）で実行します。
- この手順で dev の撤去と再作成を通す確認は、まだ行っていません（Issue #57）。

#### 1. 必要ならデータとログを退避する

補助テーブルは PITR が有効なので、テーブルを削除すると DynamoDB が 35 日保持のシステムバックアップを自動で作ります（[DynamoDB のドキュメント](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/PointInTimeRecovery_Howitworks.html#howitworks-pitr-deleting-table)）。それより長く残したい場合だけ、撤去の前にオンデマンドバックアップを取ります。オンデマンドバックアップは、削除するまで残ります。

```bash
cd applications/registry_portal
TABLE="$(AWS_PROFILE=registry-deploy-tf terraform -chdir=infrastructure output -raw supplement_table_name)"
aws dynamodb create-backup --table-name "$TABLE" --backup-name "$TABLE-before-teardown" --profile registry-deploy
```

ロググループ（本体の API と管理 Lambda、決裁 Lambda、イベント処理 Lambda）には `skip_destroy` がないため、撤去で消えます。設定した保持期間（`log_retention_in_days`）に関係なく、ログは引き継がれません。必要なログは撤去の前に取得してください。

#### 2. 追加スタックを撤去する

```bash
cd applications/registry_portal
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
aws sts get-caller-identity --profile registry-deploy   # MFA コードを入力し、短期認証情報をキャッシュする
for STACK in management decision notification_events; do
  AWS_PROFILE=registry-deploy scripts/deploy.sh --destroy --stack "$STACK" \
    "infrastructure/$STACK/backend.hcl" "infrastructure/$STACK/terraform.tfvars" || break
done
```

各スタックで plan を確認し、スタック名（`management` など）を入力します。`backend.hcl` の `key` の検査は配信と同じです（4.5）。1 つのスタックで中止・失敗したら、上の例は残りを実行せずに止まります。

#### 3. 本体の保護を外す

本体の `infrastructure/dev.tfvars` に次の 2 行を追加し、通常の配信（`apply` の入力）で反映します。tfvars は `.gitignore` の対象です。

```hcl
table_deletion_protection = false
spa_force_destroy         = true
```

```bash
AWS_PROFILE=registry-deploy scripts/deploy.sh infrastructure/backend.hcl infrastructure/dev.tfvars
```

plan で、補助テーブル（`aws_dynamodb_table.auxiliary`）の `deletion_protection_enabled` と SPA バケット（`aws_s3_bucket.spa`）の `force_destroy` が、作り直しではなく変更（update in-place）になっていることを確認します。`force_destroy` は state に入った値で destroy に効くため、destroy の前にこの apply が必要です（true にした後、apply が成功するまでは効きません）。`force_destroy` が state で `true` なら、destroy のときに Terraform がバケットのすべてのオブジェクト（ロックされたものを含む）を削除してからバケットを削除します。削除したオブジェクトは復元できません（[AWS provider 6.71.0 の aws_s3_bucket](https://registry.terraform.io/providers/hashicorp/aws/6.71.0/docs/resources/s3_bucket)）。

次に、`infrastructure/main.tf` の補助テーブル（`aws_dynamodb_table.auxiliary`）から `lifecycle { prevent_destroy = true }` の行を手元で外します（削除またはコメントアウト）。`prevent_destroy` は Terraform の制約で変数にできません（lifecycle の設定にはリテラル値しか使えません。[Terraform の lifecycle](https://developer.hashicorp.com/terraform/language/meta-arguments/lifecycle)）。**この変更はコミットしません。** 外している間は `infrastructure/tests/test_teardown_guards.py` が失敗します。

この後で撤去を中止する場合は、本節の最後の「撤去を中止したとき・destroy が途中で失敗したとき」の手順で保護を戻します。

#### 4. 本体スタックを撤去する

```bash
AWS_PROFILE=registry-deploy scripts/deploy.sh --destroy infrastructure/backend.hcl infrastructure/dev.tfvars
```

本体の撤去では `--stack` を付けません。スクリプトは plan を作る前に、追加 3 スタックの state を `terraform state pull` で読み、リソースが残っていれば「追加スタックの state にリソースが残っています」と表示して止まります。その場合は手順 2 に戻ります。

- 追加スタックの state は、本体と同じバケットにあり key だけが異なる前提です（4.5、各 `backend.hcl.example`）。スクリプトは本体の `backend.hcl` の key だけを追加スタックのもの（1.4 の表）に差し替えて読みます。
- state を読む前に、本体の `backend.hcl` と同じディレクトリの `<スタック>/backend.hcl`（上の例では `infrastructure/<スタック>/backend.hcl`）を確かめます。ファイルがあれば、その `bucket` と `region` が本体の `backend.hcl` と一致することを確かめ、一致しなければ「本体の backend.hcl と一致しません」と表示して止まります。ファイルがなければ、本体のバケットとリージョンで key だけを差し替えて読むことを表示して続けます。追加スタックの state を別のバケットに置いている構成には対応していません。
- スタックごとに「0 件」（撤去済み）か「state なし（未配信）」を表示します。どちらも撤去を止めません。state のファイルがないと `terraform state pull` は何も出力しないため、これを「state なし」と判定します。この出力は[公式ドキュメント](https://developer.hashicorp.com/terraform/cli/commands/state/pull)に記載がなく、Terraform 1.17.1 で確認した挙動です。`terraform show -json` は、state のファイルがない場合とリソースのない state の場合に同じ`{"format_version":"1.0"}` を出力して区別できないため使いません（同じく 1.17.1 で確認）。
- 認証やネットワークの失敗、state の形式の異常などで state を読めない場合は、0 件として扱わずに止まります。

plan を作った後、スクリプトは plan の削除前の値（state の値）で、補助テーブルの `deletion_protection_enabled` が `false`、SPA バケットの `force_destroy` が `true` であることを確かめます。そうでなければ、スタック名の入力を求める前に止まります。手順 3 の apply が済んでいないので、手順 3 に戻ります（飛ばして destroy すると、保護のないリソースだけを消してから途中で失敗します）。撤去の途中で削除済みのリソースは plan に出ないため、検査を飛ばし、飛ばしたリソースと属性を「plan にないため、削除済みとみなして … の検査を飛ばしました」と表示します。撤去を始める前なのにこの表示が出た場合は、リソース名が変わっていて検査が働いていません。`n` などを入力して中止してください。

plan に既存 Registry の変更や削除がないことを確認し、`main` と入力します。destroy が途中で失敗した場合は、本節の最後の「撤去を中止したとき・destroy が途中で失敗したとき」に従います。

#### 5. 保護を元に戻す

撤去が終わったら、次の 2 つを必ず戻します。

- `infrastructure/main.tf` の `prevent_destroy` の編集を戻します（`git checkout -- infrastructure/main.tf`）。`python3 -m unittest infrastructure/tests/test_teardown_guards.py` が通ることを確認します。
- `infrastructure/dev.tfvars` から手順 3 の 2 行を削除します。既定値（`table_deletion_protection = true`、`spa_force_destroy = false`）に戻ります。

作り直すときは、4 章の手順で本体、追加スタックの順に配信します。Cognito のグループは撤去で消えるため、ユーザーをグループに割り当て直します（4.1 の 7）。

#### 6. 撤去後に残るものを確認する

次のものは Terraform の管理対象ではないため、撤去後も残ります。削除されていないことと、ほかの利用者に影響がないことを確認してください。

| 残るもの | 補足 |
| --- | --- |
| 既存 Registry | 本体は ARN で参照するだけです |
| Cognito User Pool 本体とドメイン | 本体スタックが追加した client、リソースサーバー、グループは撤去されます |
| 手動で作った IAM の主体 | `registry-operator`、`RegistryPortalReadOnly`、`RegistryPortalDeployer`、`RegistryPortalBreakGlass` |
| Discovery 用の Secrets Manager と KMS キー | 発見用 M2M client の secret と、顧客管理キーを使う場合のキー |
| state バケットと bootstrap の state | 撤去の対象外です。各スタックの state のファイルは、リソースのない状態でバケットに残ります |
| 補助テーブルのバックアップ | PITR のシステムバックアップ（35 日保持）と、手順 1 で取ったオンデマンドバックアップ |

各スタックに管理対象が残っていないことは、5.3 の `terraform state list` で確認できます。

#### 撤去を中止したとき・destroy が途中で失敗したとき

手順 3 の apply の後で撤去を中止した場合や、手順 4 の destroy が途中で失敗した場合は、保護を外した状態のまま残さないでください。撤去を続けるか、既定値で apply し直して保護を戻します。

- **撤去を続ける場合**: 失敗の原因を取り除いてから、手順 4 をやり直します。削除済みのリソースは plan に出ません。残っているリソースの保護が外れていることは、手順 4 の検査が確かめます。終わったら手順 5 に進みます。
- **撤去をやめる場合**: 手順 5 と同じく、`infrastructure/main.tf` の編集を戻し、`infrastructure/dev.tfvars` から手順 3 の 2 行を削除します。そのうえで、既定値のまま本体を通常の配信で apply し直します。

```bash
cd applications/registry_portal
git checkout -- infrastructure/main.tf
python3 -m unittest infrastructure/tests/test_teardown_guards.py
AWS_PROFILE=registry-deploy scripts/deploy.sh infrastructure/backend.hcl infrastructure/dev.tfvars
```

plan で、補助テーブルの `deletion_protection_enabled` が `true` に、SPA バケットの `force_destroy` が `false` に戻る変更（update in-place）を確認してから、`apply` と入力します。destroy で削除済みのリソースは、plan では作成になります。

- 補助テーブルを削除済みの場合、作り直したテーブルは空です。削除前のデータは、手順 1 のオンデマンドバックアップと PITR のシステムバックアップにあります。
- SPA バケットを削除済みの場合も、この配信で SPA をビルドしてアップロードし直します。
- 手順 2 で撤去した追加スタックは、4.5 の手順で配信し直します。Cognito のグループを削除済みの場合は、ユーザーをグループに割り当て直します（4.1 の 7）。

## 6. トラブルシューティング

| 症状 | 主な原因と対処 |
| --- | --- |
| Terraform が `AssumeRoleTokenProviderNotSetError` で止まる | `mfa_serial` 付きのプロファイル（`registry-deploy`）を Terraform に渡している。`registry-deploy-tf` を使う（3.4） |
| `registry-deploy-tf` で `ExpiredToken`、または MFA を求められる | キャッシュの期限切れ。`aws sts get-caller-identity --profile registry-deploy` をもう一度実行する |
| `terraform init` で state バケットの 403 | `RegistryPortalDeployer` 以外で実行しているか、環境変数の認証情報がプロファイルより優先されている。`aws sts get-caller-identity` の Arn を確認する |
| `deploy.sh` がプロファイルの検査で止まる | `AWS_PROFILE` に `role_arn` と `mfa_serial` 付きの `registry-deploy` を指定する。Terraform 用プロファイルの `credential_process` が同じプロファイルを参照することと、MFA キャッシュの有効期限を確認する（3.4、4.2） |
| `check_plan.py` が中止する | plan に既存 Registry の変更・削除・置換、禁止 action の Allow、managed policy の付与、Function URL、`NotAction` や全権限のワイルドカードが含まれている。plan を見直す |
| provider が `allowed_account_ids` でエラーになる | `aws_account_id` とログイン先のアカウントが違う |
| `apply` でグループやリソースサーバーの重複エラー | 同じ名前のものが既にある。2.2 の手順で import する |
| `iam:PutRolePolicy` などが `explicitDeny` になる | `RegistryPortalDeployer` は、自分自身・ほかの 2 ロール・`registry-operator` の IAM を変更できない（IAM 手順の 4）。Terraform の対象にそれらを含めない |
| 決裁 Lambda が起動直後に設定エラーになる | 環境変数の `SUPPLEMENT_TABLE_NAME` や `DISCOVERY_*` が未設定か、形式が誤っている |
| Admin の取り込みが「公開版を取得できなかったため取り込めません」で失敗し続ける | 発見用 M2M の secret が顧客管理キーで暗号化されているのに、decision スタックに `discovery_kms_key_arn` を渡していない（または本体と違うキーを渡している）。決裁 Lambda のログに「発見用 secret の取得に失敗しました」が出ていれば該当する。decision の `terraform.tfvars` に本体と同じ `discovery_kms_key_arn` を指定して apply する（4.3） |

## 7. 統合実装の状態と確認事項

過去の食い違い 11 項目と、レビューで追加した項目 13 を、Issue #5・#49 の統合実装に合わせて整理しました。項目 12 は、Issue のコメントの要求と実装の差を設計判断として記録したものです。以下の実装はローカル検査の対象です。dev への AWS apply と手動受入は未実施で、AWS 上のアクセス制御、M2M の疎通、TTL による削除、EventBridge の受信、顧客管理キーでの取り込みは配信後に確認します。配信後に確認する項目は、すべて README「dev の手動受入を記録する」のチェックリストにあります。

| 項目 | 状態 | 統合実装と運用時の確認事項 |
| --- | --- | --- |
| 1 | 解消済み | 本体の補助テーブルの TTL 属性はアプリと同じ `expiresAt`。AWS による期限切れ項目の削除は配信後に確認する |
| 2 | 解消済み | 管理 Lambda に `SUPPLEMENT_TABLE_NAME`、`REGISTRY_ARN`、`DISCOVERY_*` を設定する。`A2A_SEARCH_ENABLED` は未設定なら無効（`config.py` の `load_catalog_settings`）。`isA2A` の補完と確認（#16）が終わるまで設定せず、無効のまま配信する |
| 3 | 解消済み | CORS は SPA の origin だけを許可し、`GET`・`POST`・`PATCH`・`OPTIONS` と `Authorization`・`Content-Type`・`Idempotency-Key` を許可する。各 route は `registry-portal/api.invoke` の scope を検証する（scope の値は本体の `local.portal_api_scope` だけに定義し、追加スタックは output `rest_api`（`portal_api_scope` を含む）を tfvars に転記して、SPA は `frontend_config` から受け取る。`test_portal_scope.py` で検査する）。管理 Lambda の呼び出し許可 `GET/api/records/*` は決裁 Lambda の GET の route 3 本の実行 ARN にも一致するが、API Gateway は route の統合先だけを呼び出すため影響はない。一致する route の集合は `test_route_permissions.py` で固定する |
| 4 | 解消済み。対象ロールは目視確認 | ガードは `module.management_drafts.aws_iam_role_policy.management_drafts` に作成・更新・申請を、`module.decision.aws_iam_role_policy.decision` に決裁だけを許可する。アドレスは `check_plan.py` の `ScopedPolicy`（module 呼び出し名と resource 名）で持ち、追加スタックの定義に実在することを `test_plan_guard.py` で検査する。policy の資源アドレスで役割を判定するため、`management_role_name` の取り違えは検出できない。本体の output と plan を照合する。決裁・イベント処理のロググループと DLQ の ARN は初回 plan でも確定する |
| 5 | 解消済み | bootstrap の Identity Center 資源は任意。個人アカウントでは `identity_center_instance_arn` と `deployer_group_id` を省略し、state バケットだけを作る |
| 6 | 個人アカウント対応済み | state バケットは `deployer_principal_arn` を照合する。`deploy.sh` は MFA 付きのロールプロファイルと `credential_process` の Terraform 用プロファイルに対応する。SSO permission set は本体向けで、`deploy.sh` は SSO プロファイルでの `--stack` を拒否する。dev は個人アカウントだけで運用するため、追加スタック向けの permission set の拡張は行わない（4.2） |
| 7 | 解消済み | 本体・bootstrap・追加 3 スタックの AWS provider を `= 6.71.0` に固定した。共通 ZIP の出力先 `backend/build/`、各 `backend.hcl`、手順書の plan ファイル（`*.tfplan`）と `plan.json` は ignore 対象 |
| 8 | 解消済み | CI は `notification_events` を含む全ルートスタックの init と validate を行う。AWS の認証情報は持たず、apply は行わない |
| 9 | 別 state を維持する決定 | 本体は REST API のパスとステージを持つ。管理と決裁の追加スタックはそれぞれのメソッドと統合を持つ。本体の output を各 tfvars に手動転記する。現在は state を移動しない。後続 Issue で統合する場合だけ import と `state rm` を行う（[ADR-0013](../adr/0013-separate-portal-addon-states.md)） |
| 10 | 運用上の制約として継続 | 個人アカウントの `RegistryPortalDeployer` は `AdministratorAccess` を持ち、新しいロール経由の Registry 書き込みまで IAM では防げない。全スタックを `scripts/deploy.sh`（追加スタックは `--stack`）で反映し、plan をガードに通して IAM の対象ロール・ARN・action を目視確認する。権限の絞り込みや permissions boundary は後続の検討対象 |
| 11 | #49 で解消済み | decision の `discovery_kms_key_arn` は任意で既定は `null`。顧客管理キーの場合だけ本体と同じ ARN を渡すと、別の inline policy が Secrets Manager 経由・対象 secret の暗号化コンテキストとの完全一致（`StringEquals`）に限った `kms:Decrypt` を付ける（文は本体と共通の `modules/discovery_client` が作り、secret ARN はワイルドカードを含まない完全な ARN だけを受け付ける）。AWS 管理キーの場合は両方で省略または `null` にする。顧客管理キーでの Admin の取り込みと、`null` の場合に復号権限を追加しないことを配信後に確認する |
| 12 | 設計判断として記録 | Issue #5 のコメントは「管理 Lambda 以外のロールが secret を取得できないこと」の確認を求めているが、#49 の本文のとおり、決裁ロールも Admin の取り込み（#16）のために発見用 secret の `GetSecretValue`（と項目 11 の `kms:Decrypt`）を持つ。secret を取得できるのは管理ロールと決裁ロールだけとし、イベント処理ロールなどには付けない（1.6）。設計書の「最小インフラとセキュリティ」の Secrets Manager の行もこの範囲に更新した。配信後の手動受入（README「dev の手動受入を記録する」）で確認する |
| 13 | 解消済み | CloudFront のレスポンスヘッダーの CSP は、REST API の CORS との循環参照を避けるため `connect-src` を execute-api のリージョン単位で許可する。SPA の本番ビルドが `index.html` に埋め込む meta の CSP（`frontend/src/connectCsp.ts`）で、接続先を自分の REST API と Cognito に限る。ブラウザは両方を満たす通信だけを許可する。配信後に meta の存在を確認する |

## 関連文書

- [MVP1 設計書](../../plan/aws-agent-registry-mvp1-design-ja.md)
- [Registry ポータルの README](../../applications/registry_portal/README.md)
- [既存の AWS Agent Registry の確認手順](registry-portal-existing-registry-check-ja.md)
- [Issue #2 の作業記録テンプレートと再確認の手順](registry-portal-issue2-record-template-ja.md)
- [個人 AWS アカウントで Registry ポータル用の IAM ユーザーとロールを作成する](registry-portal-iam-setup-personal-account-ja.md)
- [ADR-0003 公開 HTTPS と単一 Registry](../adr/0003-public-https-without-waf-and-single-registry.md)
- [ADR-0004 CI から AWS に反映しない](../adr/0004-no-aws-deployment-from-ci.md)
- [ADR-0013 本体と追加スタックの state を分けて運用する](../adr/0013-separate-portal-addon-states.md)
- [ADR-0014 dev の撤去は保護を一時的に外して行い、state バケットと bootstrap は残す](../adr/0014-dev-teardown-protection-and-scope.md)
