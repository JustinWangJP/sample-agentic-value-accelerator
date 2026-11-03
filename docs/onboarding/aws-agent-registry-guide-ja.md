# AWS Agent Registry オンボーディングガイド

このrepoは、AWS Agent Registryを使い、Agent・MCP Server・A2A Server・Skill・Custom Resourceを共通のレジストリに登録し、承認と廃止を管理する機能を提供します。主な入口はAVA Control Planeです。デプロイ済みアプリの自動登録や、検索・登録前検証を試せる参照実装も含まれています。

**対象読者:** 資産を登録する開発者、登録を承認する管理者、Control Planeを構築するプラットフォーム担当者。

**確認範囲:** 2026-11-03時点のチェックアウトにあるソースを静的に確認しました。AWSへのデプロイ、実アカウントへの登録、画面の実機操作は実施していません。以下はこのrepoの実装の説明です。AWSサービス全体の最新仕様・提供リージョンを保証する資料ではありません。

## 1. 最初に理解すること

| 用語・画面 | このrepoでの役割 |
|---|---|
| AWS Agent Registry | 資産のレコード、descriptor、タグ、承認状態を保存するAWS側のサービス |
| AVA registry | Control Planeが利用するレジストリ。Terraformモジュールの既定名は `AVA`。名前を持つregistryリソースとして構築される |
| Registry `/registry` | 5種類の資産の登録・閲覧への入口 |
| Approval Policies `/secure/approval-policies` | 登録を自動承認、承認待ち、拒否のどれにするかを決めるAVA側のポリシー |
| Approval Queue `/operate/approvals` | 承認待ち要求と承認・拒否の判断を扱うAVA側の画面。要求はDynamoDBに保存される |
| Govern → Agent Registry `/govern/agents` | デプロイ情報や複数の資産APIを集約するガバナンス画面。AWS Agent Registryの保存レコード一覧とは対象が異なる |

レジストリへの登録は、資産の名前・接続先・能力などをカタログ化する操作です。登録そのものが、Agentの実行環境の作成、接続先の起動、Skillの実行を行うわけではありません。特に画面の **Deploy to Registry** は、選択した定義をレジストリへ登録する操作です。

根拠: [画面ルーティング](../../platform/control_plane/frontend/src/App.tsx)、[登録画面の処理](../../platform/control_plane/frontend/src/components/agents/AgentsLanding.tsx)、[Governの集約処理](../../platform/control_plane/frontend/src/components/govern/useAgentRegistry.ts)。

## 2. 提供機能の一覧

### 2.1 登録できる5種類の資産

| 種類 | 保存形式 | 画面 | 登録する情報・用途 |
|---|---|---|---|
| Agents | `recordType=AGENT`、`Kind=agent`、descriptor `custom` | `/registry/agents` | 名前、runtime、`runtime_ref`、capabilities、認証方式のヒントなど。A2A AgentCardを必須としないAgentの登録 |
| MCP Servers | `recordType=MCP`、`Kind=mcp`、descriptor `mcpServer` | `/mcp` | URL、認証・ヘッダー情報、カテゴリなど。既知のMCP Serverのカタログからの登録にも対応 |
| A2A Servers | `recordType=AGENT`、`Kind=a2a`、descriptor `a2aAgentCard` | `/a2a` | エンドポイント、取得したAgentCard、認証・委任方式など。AgentCard取得APIを提供 |
| Skills | `recordType=SKILL`、`Kind=skill`、descriptor `agentSkillsDefinition` | `/registry/skills` | evaluation / extraction / workflow / guardrailなどの種別、入力変数、出力スキーマ、タグなどの定義 |
| Custom Resources | `recordType=CUSTOM`、`Kind`は利用者指定、descriptor `custom` | `/registry/custom-resources` | knowledge-base、prompt-lib、eval-suiteなど、任意種別の資産。APIでは自由形式のJSON `metadata`を登録可能 |

AgentsとA2A ServersはAWS側では同じ `AGENT` 型ですが、AVAは `Kind` タグで分けて表示します。共有ヘルパーは `ManagedBy=AVA` などのタグを付与し、descriptorの `data` にJSONを保存・復元します。

実装: [Agents API](../../platform/control_plane/backend/src/api/routes/agents.py)、[MCP API](../../platform/control_plane/backend/src/api/routes/mcp.py)、[A2A API](../../platform/control_plane/backend/src/api/routes/a2a.py)、[Skills API](../../platform/control_plane/backend/src/api/routes/skills.py)、[Custom Resources API](../../platform/control_plane/backend/src/api/routes/custom_resources.py)、[共通クライアント](../../platform/control_plane/backend/src/services/agent_registry_client.py)。

### 2.2 操作と実装範囲

| 機能 | 提供内容 |
|---|---|
| 一覧・詳細取得 | 5種類それぞれに一覧・詳細APIを提供 |
| 登録 | 5種類それぞれに登録APIを提供。Agents・MCP・A2A・Skillsはキュレーション済み定義を取得するAPIも提供 |
| 画面からの登録 | Agents・Skillsはカタログからの登録、Custom Resourcesは登録フォームを提供。任意のAgentやSkillの定義はAPIから登録可能 |
| 更新 | MCP・A2Aは `PATCH` APIを提供。Agents・Skills・Custom Resourcesのルートには対応する `PATCH` は定義されていない |
| 廃止 | 画面のDeleteや各種 `DELETE` APIは、共通ヘルパー経由で `DEPRECATED` にする論理的な廃止。AWSレコードの物理削除とは異なる |
| 承認制御 | 登録前にポリシーを評価。自動承認・承認待ち・拒否に分岐 |
| 承認キュー | 承認、拒否、取消、一括承認・一括拒否、件数集計のAPIを提供 |
| デプロイ後の公開 | 対象のデプロイStep Functionsが成功した後、EventBridgeとLambdaでAgentレコードを自動登録 |
| 検索・キュレーションの例 | `agentcore-in-a-box` にデータプレーン検索と登録前検証・承認操作を提供。Control Planeの上記登録APIとは別の参照実装 |

キュレーション済みカタログは登録のひな形です。登録済み資産の実行権限や接続性は、個別のruntime・認証・ネットワーク設定にも依存します。

## 3. 初回利用前の準備

### 3.1 プラットフォーム担当者が確認すること

Control Plane全体の構築・起動は、[Control Plane README](../../platform/control_plane/README.md) と [開発者向けオンボーディング](contributor-guide-ja.md)を参照してください。Registry固有の確認事項は次のとおりです。

1. **SDKとCLIがサービスを認識すること。** バックエンドは `boto3.client("agent-registry-control")` を使用します。依存定義は `boto3>=1.43.0`、`botocore>=1.43.0` ですが、バージョン下限だけで判断せず、実際の環境にサービスモデルがあることを確認します。Terraform実行環境のAWS CLIにも `agent-registry-control` が必要です。
2. **対象アカウント・リージョンでRegistry APIを利用できること。** このControl Planeの共通クライアントは `AWS_REGION` を使用します。`GOVERN_AWS_REGION` とは設定の役割が異なります。
3. **Terraformでregistryが構築されていること。** `modules/agent_registry` はAWS CLIを実行して同名registryの再利用または作成を行います。CLIや作成処理の失敗時はエラーにする実装です。
4. **ECSのバックエンドにRegistry設定が渡っていること。** `AGENT_REGISTRY_ID`、`AGENT_REGISTRY_ARN`、`AGENT_REGISTRY_NAME` が対象環境の値になっていることを確認します。
5. **IAMとDynamoDBが利用できること。** Registry操作のIAM権限に加え、承認ポリシー・承認要求のテーブルへのアクセスが必要です。既存のTerraformにはECSタスクのRegistry権限設定が含まれています。
6. **運用する承認ルールを登録すること。** 手動承認を使う場合は、対象に一致する有効な `require_approval` ポリシーが必要です。該当ポリシーがない場合、AVAの登録APIは自動承認を選びます。

既にTerraformを適用済みの環境では、次の読み取り操作で設定値を確認できます。

```bash
cd platform/control_plane/infrastructure
terraform output -raw agent_registry_id
terraform output -raw agent_registry_arn
terraform output -raw agent_registry_name
```

`modules/agent_registry` の `auto_approve` は既定で `false` ですが、これはAWS registry側の設定です。**AVAの承認ポリシー評価とは別の設定**なので、これだけで全登録が手動承認になるとは考えないでください。

根拠: [Terraform構成](../../platform/control_plane/infrastructure/main.tf)、[registry構築](../../platform/control_plane/infrastructure/modules/agent_registry/main.tf)、[registry変数](../../platform/control_plane/infrastructure/modules/agent_registry/variables.tf)、[出力](../../platform/control_plane/infrastructure/outputs.tf)、[ECS設定・IAM](../../platform/control_plane/infrastructure/modules/ecs/main.tf)、[バックエンド設定](../../platform/control_plane/backend/src/core/config.py)、[依存定義](../../platform/control_plane/backend/requirements.txt)。

### 3.2 利用者・承認者の権限

| 操作 | APIが要求するロール |
|---|---|
| 資産の一覧・詳細・登録 | `VIEWER` の権限要件 |
| MCP・A2Aの更新、資産の廃止 | `OPERATOR` の権限要件 |
| 承認・拒否、一括承認・一括拒否 | `ADMIN` の権限要件 |
| 承認要求の取消 | `OPERATOR` の権限要件 |

承認要求には `required_role` や `quorum` も保存されますが、現実装の承認APIは `ADMIN` を要求し、1回の判断で状態を確定します。複数人の合意が必要な運用では、この点を事前に確認してください。

## 4. 最初の登録を試す

### 4.1 画面から始める

1. Control Planeにログインして `/registry` を開きます。
2. **Agents** または **Skills** を開き、キュレーション済みカタログから対象を選びます。
3. **Deploy to Registry** を押します。これは選択した定義を登録APIに送る操作です。
4. 登録済み一覧へ戻り、作成されたレコードの状態を確認します。画面/APIの `active` はAWSの `APPROVED` に対応します。`pending` の場合はAPIの `status_raw` も確認してください。
5. 承認待ちであれば、管理者が `/operate/approvals` で対象要求と登録内容を確認し、承認または拒否します。
6. 不要になった資産は一覧のDeleteから廃止できます。runtimeや接続先の削除は別途扱います。

独自の資産を画面から登録する場合は `/registry/custom-resources` の **Register custom resource** で名前、kind、説明、タグを入力できます。任意JSONの `metadata` を指定する場合はAPIを使用します。

根拠: [Agents画面](../../platform/control_plane/frontend/src/components/agents/AgentsLanding.tsx)、[Skills画面](../../platform/control_plane/frontend/src/components/skills/SkillsLanding.tsx)、[Custom Resources画面](../../platform/control_plane/frontend/src/components/customresources/CustomResourcesLanding.tsx)。

### 4.2 APIから独自のAgentを登録する

以下は手動実行するための例です。`AVA_API` はバックエンドのAPIベースURL、`AVA_TOKEN` はログイン済み利用者のトークンに置き換えます。既定のAPI prefixは `/api/v1` です。API Gatewayなどでstage/root pathを使用する環境では、そのパスも含めてください。

```bash
export AVA_API='https://<control-plane-api>/api/v1'
export AVA_TOKEN='<login-token>'

# 登録済みAgentの確認
curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer ${AVA_TOKEN}" \
  "${AVA_API}/agents/list"

# 独自Agentのメタデータを登録
curl --fail-with-body --silent --show-error \
  -X POST "${AVA_API}/agents" \
  -H "Authorization: Bearer ${AVA_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "KYC Review Agent - onboarding",
    "runtime": "custom",
    "runtime_ref": "https://<your-agent-endpoint>",
    "description": "KYCレビュー支援Agentの登録例",
    "capabilities": ["kyc", "document-review"],
    "auth_hint": "oauth2",
    "category": "kyc",
    "source": "custom"
  }'
```

登録結果の `agent_id`、`status`、`status_raw` を控えます。承認待ちの場合は、管理者がキューから判断します。`auth_hint` は接続方式のメタデータであり、この指定だけでOAuth2などの認証基盤が構築されるわけではありません。

API例の認証ヘッダーは、[フロントエンドのAPIクライアント](../../platform/control_plane/frontend/src/components/agents/api.ts)に合わせています。

### 4.3 Custom Resourceの登録例

```bash
curl --fail-with-body --silent --show-error \
  -X POST "${AVA_API}/custom-resources" \
  -H "Authorization: Bearer ${AVA_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "KYC Rules Knowledge Base",
    "kind": "knowledge-base",
    "description": "KYCルールの参照先を共有する登録例",
    "tags": ["kyc", "onboarding"],
    "metadata": {
      "owner_team": "compliance",
      "reference": "<knowledge-base-id-or-url>"
    }
  }'
```

ここで `tags` 配列や `metadata` に入れた値はdescriptor内のデータです。利用者指定の配列が、そのままAWSリソースタグになるという意味ではありません。共通ヘルパーが付ける `ManagedBy`・`Kind` などのAWSタグと区別してください。

## 5. 承認フローと状態の見方

通常の登録APIは、次の順に処理します。

1. `kind`、登録名、`action=register` を使って承認ポリシーを評価します。kindは `agent` / `mcp` / `a2a` / `skill` / `custom` です。
2. `deny` の場合はHTTP 403を返し、レコードを作成しません。
3. それ以外はAWS側でレコードを作成し、`CREATING` から `DRAFT` になるのを待ちます。
4. 承認申請APIで `PENDING_APPROVAL` に進めます。
5. `auto_approve` なら `APPROVED` への変更を試みます。`require_approval` ならDynamoDBに承認要求を作成します。
6. 管理者がキューで承認・拒否すると、AWSレコードの状態も更新する処理を実行します。

| 操作・状態 | AWSレコードの状態 / APIの `status_raw` | AVA APIの `status` |
|---|---|---|
| 登録直後、作成処理中 | `CREATING` | `pending` |
| 作成完了、承認申請前 | `DRAFT` | `pending` |
| 承認待ち | `PENDING_APPROVAL` | `pending` |
| 承認 | `APPROVED` | `active` |
| 拒否 | `REJECTED` | `rejected` |
| 取消・廃止 | `DEPRECATED` | `deprecated` |

`pending` という表示だけでは作成中・DRAFT・承認待ちを区別できないため、詳細な確認には `status_raw` を使います。

これは複数のAWS API呼び出しとDynamoDB書き込みからなる処理です。**全体が一つのトランザクションとして確定するわけではありません。** 共通クライアントは状態のポーリングを行いますが、申請や状態変更の一部エラーはログに残して処理を続けます。また、承認キュー側はAWSレコード更新が失敗しても判断結果を保存するため、両方の状態が一致しているか確認が必要です。

ポリシー未一致に加え、ポリシーテーブルが未設定、またはポリシー読取で `ClientError` が発生した場合にも、ポリシーエンジンは空のポリシー一覧として扱い、自動承認へ進む実装です。

根拠: [ポリシー評価](../../platform/control_plane/backend/src/services/approval_policy_engine.py)、[登録・ポーリング・キュー作成](../../platform/control_plane/backend/src/services/agent_registry_client.py)、[承認判断とAWSへの反映](../../platform/control_plane/backend/src/api/routes/approval_requests.py)。

## 6. デプロイ成功後の自動登録

Control PlaneのTerraformは、共通のデプロイStep Functionsに対する成功イベントを処理する `deployment_success_hook` を組み込みます。

1. EventBridgeが対象Step Functionsの `SUCCEEDED` イベントを受けます。
2. LambdaがDynamoDBからdeploymentを取得し、保存された状態が `deployed` であることを確認します。
3. 同じ `DeploymentId` タグの登録が見つかれば、重複作成をスキップします。
4. `recordType=AGENT`、`Kind=agent`、`Source=foundry-deploy` のレコードを作成します。
5. `DRAFT` → `PENDING_APPROVAL` → `APPROVED` への遷移を試み、承認済みの履歴行をApproval Queueのテーブルへ記録します。

`runtime_ref` には、利用できる出力に応じてWeb UIのURL、AgentCore runtime ARN、runtime ID、deployment IDが選ばれます。そのため、自動登録された全レコードがAgent呼び出し用エンドポイントを持つとは限りません。

**通常の登録APIとの差:** このLambdaの公開処理はAVAの承認ポリシーエンジンを呼ばず、自動承認を試みます。通常登録に `require_approval` ポリシーを設定しても、この経路が同じ承認待ちになるとは限りません。また、対象はTerraformで接続された共通デプロイStep Functionsです。別のデプロイ経路への適用は個別に確認してください。

自動登録・履歴記録にはログ出力して終了する失敗経路があり、アプリのデプロイ成功とRegistry登録の成功は別に確認します。

根拠: [hookの接続](../../platform/control_plane/infrastructure/main.tf)、[Lambda実装](../../platform/control_plane/infrastructure/modules/deployment_success_hook/lambda_src/index.py)。

## 7. API早見表

パスは既定prefix `/api/v1` からの相対パスです。`{id}` は各一覧・登録結果から取得します。

| 対象 | 一覧 | 詳細 | 登録 | その他 |
|---|---|---|---|---|
| Agents | `GET /agents/list` | `GET /agents/{id}` | `POST /agents` | `GET /agents/curated`、`DELETE /agents/{id}` |
| MCP Servers | `GET /mcp/list` | `GET /mcp/{id}` | `POST /mcp` | `GET /mcp/curated`、`PATCH /mcp/{id}`、`DELETE /mcp/{id}` |
| A2A Servers | `GET /a2a/list` | `GET /a2a/{id}` | `POST /a2a` | `GET /a2a/curated`、`POST /a2a/fetch-card`、`PATCH /a2a/{id}`、`DELETE /a2a/{id}` |
| Skills | `GET /skills/list` | `GET /skills/{id}` | `POST /skills` | `GET /skills/curated`、`DELETE /skills/{id}` |
| Custom Resources | `GET /custom-resources/list` | `GET /custom-resources/{id}` | `POST /custom-resources` | `DELETE /custom-resources/{id}` |
| Approval Queue | `GET /approval-requests/list` | `GET /approval-requests/{id}` | `POST /approval-requests` | `POST /approval-requests/{id}/approve`、`/deny`、`/cancel` |

承認キューには `POST /approval-requests/batch-approve`、`POST /approval-requests/batch-deny`、`GET /approval-requests/summary/counts` もあります。

根拠は2.1の各ルートと [バックエンドへのルート登録](../../platform/control_plane/backend/src/main.py)です。画面URL `/registry/agents` とAPI URL `/api/v1/agents` を混同しないでください。

## 8. つまずきやすい点

| 症状・疑問 | 確認すること |
|---|---|
| 登録APIが503を返す | `AGENT_REGISTRY_ID` が未設定ではないか。Terraform出力とバックエンドの環境変数を確認する |
| `agent-registry-control` を認識しない | 実行環境のAWS CLI・SDKがサービスモデルを持つか確認する。Terraformのregistry作成はCLI対応を検査する |
| 403で登録できない | 一致した承認ポリシーの `deny` とAPIのロール要件を確認する |
| 手動承認を想定したのに `APPROVED` になった | ポリシーの有効状態・kind・名前パターン・actionを確認する。未一致・読取失敗時の自動承認、デプロイhookの別経路にも注意する |
| キューは承認済みだがAWS側は承認待ち | AWS状態更新のログとレコード詳細を確認する。キューとAWSは一括確定されない。確定済み要求への再判断は409になるため、単純な再承認では復旧できない |
| 一覧が空・更新されない | 設定、IAM、リージョン、ブラウザのAPIレスポンスを確認する。Agents・Skillsの画面は `Promise.allSettled` を使い、取得失敗が明確なエラーとして表示されない経路がある |
| Governで見えるAgentがRegistry一覧と違う | Governは複数ソースを集約する。デモ用データも合成する実装があるため、AWS保存レコードと区別する |
| DeleteしたのにAWSレコードが残る | 意図された廃止処理。状態が `DEPRECATED` になったかを確認する |
| Registryの説明にComing soonと書かれている | 旧コンポーネントや紹介文が残っている。現行 `App.tsx` はAgents・Skills・Custom Resourcesを実際のLanding画面へ接続している |

一覧取得の共通ヘルパーには50ページの上限があり、既定100件/ページで最大5,000件です。Agents/A2Aを区別するタグの照合はクライアント側で行い、タグ取得に追加API呼び出しが発生します。大規模な登録数を想定する場合は、この実装範囲を確認してください。

## 9. repo内の関連実装

| 場所 | 提供内容・使い分け |
|---|---|
| [Control Planeの共通クライアント](../../platform/control_plane/backend/src/services/agent_registry_client.py) | 通常の登録、取得、承認申請、状態変更、廃止、承認要求作成の中心。機能拡張時の入口 |
| [App Factory公開スクリプト](../../applications/app_factory/scripts/publish_to_registry.py) | runtimeとuse caseからA2A用の定義を作り、`app-factory-<use_case_id>` の既存レコードを更新、なければ作成して承認申請するCLI。Control Planeの通常登録とは別の公開経路 |
| [agentcore-in-a-boxのRegistry処理](../../applications/reference_implementations/agentcore-in-a-box/lambda/agentcore-primitives/registry.py) | レコードの読取、データプレーンの `search_registry_records` 呼び出し、validate / submit / approve / reject / deprecateを実装。submit前にdescriptorの必須項目・TLS・管理対象JWT境界などを検証する参照例 |
| [agent-safetyのダッシュボード](../../applications/reference_implementations/agent-safety/dashboard/api.py) | AWS Agent Registryのregistry・レコードを一覧するAPIを持つ。独自のローカルregistryや予算・同期のAPIも併存するため、同名のregistryがすべてAWSサービスを指すわけではない |
| [KYCのRegistry Proxy](../../applications/reference_implementations/kyc-governance-insights/deploy/console-services/registry-proxy/template-cfn.yaml) | DynamoDBを前段APIで公開する別のregistry実装。AWS Agent Registry統合と混同しない。ここはグラフの索引対象外のためテンプレートを直接確認した |

参照実装には独立した設定・API形状があります。Control Planeと同じ認証、承認ポリシー、SDKパラメータの互換性を前提にせず、利用する経路単位で確認してください。

## 10. オンボーディングの完了条件

- [ ] 自分の資産を5種類のどれで登録するか決められる。
- [ ] 対象registryのID・リージョンと、利用する画面/APIが分かる。
- [ ] テスト用の資産を登録し、返却IDとAWSレコードの状態を確認できる。
- [ ] 手動承認を使う場合、ポリシーの一致とキュー作成、承認後の状態を確認できる。
- [ ] 自動デプロイ経路を使う場合、deploymentの成功とRegistryへの公開結果をそれぞれ確認できる。
- [ ] テスト資産を廃止し、`DEPRECATED` への変更を確認できる。

この資料の作成時に実施したのはソース・ルーティング・設定の照合です。上記チェック項目は、導入担当者が対象環境で実施する確認事項です。
