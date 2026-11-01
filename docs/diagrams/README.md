# AVA AWS構成図（日本語・draw.io）

[編集可能な構成図を開く](ava-aws-architecture-ja.drawio)

draw.io Desktop、またはdiagrams.netの「ファイル → 開く」から読み込んでください。5ページ構成です。日本語ラベル、境界、接続線、表を個別に編集できます。外部画像や外部アイコンライブラリは使用していません。

| ページ | 内容 |
| --- | --- |
| 01 全体AWS構成 | UI配信、認証、管理API、データ、デプロイ、業務実行の接続 |
| 02 VPC・Subnet・CIDR | Control Planeの2 AZ、public/private subnet、IGW、AZ別NAT、VPC Endpoint、経路 |
| 03 SaaS・任意Gateway | 外部IdP・他クラウド・Docker Hub、任意のLiteLLMセルフホスト構成 |
| 04 Foundation・Langfuse | 別スタックのVPC、/24分割、単一NAT、Langfuseセルフホスト |
| 05 CIDR・ポート・根拠 | IPv4範囲、AWS予約IPを除いた割当範囲、通信ポート、根拠ファイル |

## 前提と環境ごとの差分

解析日：2026-10-31。リポジトリ内IaC・設定を根拠に作成した設計図であり、AWS実環境の照会結果ではありません。

- Control Planeは`infrastructure/variables.tf`の既定値、`us-east-1`、`10.0.0.0/16`、2 AZから図示しています。新規VPCではpublicが`10.0.0.0/20`・`10.0.16.0/20`、privateが`10.0.32.0/20`・`10.0.48.0/20`です。
- 既存VPCを採用する場合、そのsubnet CIDRは図の既定値からは確定できません。実際のVPC/subnet IDs、tfvars、Terraform stateと照合して置き換えてください。AZ、CIDR、タスク数、IP/EIPを実環境値として断定していません。
- Control PlaneのECSタスクと内部ALBはpublic subnet、VPC Linkもpublic subnetへの配置です。ECSはpublic IP割当があり、ECSのTCP8000受信元はALB SGです。内部ALBという性質とprivate subnetへの配置は別です。
- Gateway Endpoint（S3/DynamoDB）の明示的なroute table associationはprivate向けです。Interface Endpoint（ECR API/DKR/Logs）はprivateに配置され、private DNSを使用します。
- LiteLLM Gatewayは任意配備の別モジュールです。内部ALBはpublic、ECS・PostgreSQL・Redisはprivateの構成です。図の配置を全環境への必須配備と解釈しないでください。
- Foundation StackのECS版は別のCIDR分割式です。新規VPCのpublicは`10.0.0.0/24`・`10.0.1.0/24`、privateは`10.0.10.0/24`・`10.0.11.0/24`。両AZは単一NATを参照します。EKS版の詳細は本図の対象外です。
- Control PlaneとFoundationは同じ既定VPC CIDRを持ちます。別VPCを接続する設計では重複を解消する必要があります。本図はVPC PeeringやTGW接続が配備済みとは示していません。
- LangfuseとLiteLLMはセルフホストのソフトウェアとして分類しました。外部SaaSの利用契約・有効化はコードだけでは確定しません。Entra ID・Okta・Auth0等は設定可能な選択肢です。
- モデル名から直接のOpenAI API接続を推定していません。ローカルGateway設定にあるOpenAIモデルはBedrock Mantle経由です。Bedrock/AgentCoreはAWSサービスとして区別しています。
- 実線は定義済み接続、破線は任意・条件付き接続を表します。矢印は主な論理経路で、全てのIAMやAPI呼び出しを列挙していません。

## 主な根拠

以下はすべて`platform/control_plane/`以下です。

- `infrastructure/main.tf`、`variables.tf`、`modules/networking/main.tf`
- `infrastructure/modules/ecs/main.tf`、`modules/api_gateway/main.tf`
- `infrastructure/modules/litellm/{main.tf,alb.tf,rds.tf,redis.tf,security.tf}`
- `templates/foundation-stack/iac/terraform/{main.tf,variables.tf}`、同配下`modules/langfuse/`
- `backend/src/api/routes/identity_providers.py`
- `backend/src/services/{langfuse_provisioning.py,multicloud_connector_service.py}`
- `gateway/litellm-config-local.yaml`

ネットワークを変更した際は、ページ02・04の境界と経路、ページ05のCIDR・通信表を同時に更新してください。接続先URL・秘密情報・アカウント固有値は、公開可否を確認した上で環境別コピーに記入してください。
