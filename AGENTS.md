# リポジトリガイドライン

## 言語ポリシー

- すべての会話・コード内コメント・ドキュメント・エラーメッセージは日本語で記述する。
- 変数名・ファイル名は英語を使用してよい。
- 技術用語は必要に応じて英語を併記する。
### 基本ルール

- すべての会話は日本語で行う
- コード内のコメントは日本語で記述
- エラーメッセージの説明は日本語
- ドキュメント生成は日本語
- 変数名やファイル名は英語可

### 技術用語

- 技術用語は必要に応じて英語併記

## プロジェクト構成とモジュール配置

AVA（Agentic Value Accelerator）は、AWS 向けのエージェント基盤とデプロイ可能なアプリケーションを提供します。

- `platform/control_plane/backend/src/`: FastAPI のルート、サービス、Pydantic モデル、共通設定を配置します。テストは `backend/tests/` にあります。
- `platform/control_plane/frontend/`: React、TypeScript、Vite による UI です。ソースは `src/`、テストは `src/tests/`、静的アセットは `public/` に配置します。
- `platform/control_plane/infrastructure/` と `templates/`: Terraform のインフラ定義と、デプロイ用のスターターテンプレートを配置します。
- `applications/`: FSI Foundry、参照実装、App Factory を収録しています。各コンポーネントの手順も確認してください。
- `plan/`、`docs/`、`platform/docs/`: 計画策定、導入、アーキテクチャに関する文書を配置します。

## ビルド・テスト・開発コマンド

各コマンドは、次に示すディレクトリで実行してください。

- `platform/control_plane/`: `.env.example` を `.env` にコピーし、ルートの README に従って AWS 接続を設定します。`finch compose up --build -d` でローカル環境を起動します。
- `platform/control_plane/backend/`: 仮想環境を使用し、`pip install -r requirements.txt` で依存パッケージをインストールします。
- バックエンド: `pytest` でテスト一式を実行します。`pytest tests/test_govern_tablefree.py` で特定のモジュールを実行できます。
- `platform/control_plane/frontend/`: `npm ci` で依存パッケージをインストールし、`npm run dev` で Vite の開発サーバーを起動します。
- フロントエンド: `npm run build` で本番用アセットを生成します。TypeScript の型検査は別途 `npm run typecheck` で実行します。
- フロントエンド: `npm run lint` で ESLint、`npm test` で Vitest をそれぞれ実行します。

## コーディング規約と命名

既存コンポーネントの規約に合わせてください。Python は PEP 8 に従い、インデントはスペース4個、関数名は `snake_case` とし、公開 API に型ヒントを付けます。バックエンドの依存パッケージには Black、Flake8、mypy が含まれます。フロントエンドはスペース2個のインデントと、PascalCase のコンポーネントファイル名に合わせます。Terraform は `terraform fmt` で整形し、モジュール変数の説明を記載してください。

## テスト方針

変更した振る舞いを確認するテストを追加してください。pytest のファイル名は `test_*.py`、フロントエンドは `*.test.ts` とします。バックエンドでは pytest-asyncio、Hypothesis、pytest-cov も利用できます。必要に応じてコンポーネント間の連携も検証してください。旧テンプレートに関する既知の失敗は `backend/pytest.ini` と各コンポーネントの文書を確認し、失敗内容や実行対象からの除外を明記してください。

## コミットとプルリクエスト

最近の履歴と同様に Conventional Commits 形式を使用します。例: `feat(fsi): 引受審査のトリアージを追加`、`fix(deploy): ドメインの仮値を修正`。大きな変更は、着手前に Issue を作成してください。PR は `.github/pull_request_template.md` を使用し、`main` を対象に提出します。変更内容、関連する Issue や仕様、テスト結果を記載し、必要な文書を更新してください。UI の変更にはスクリーンショットを添付します。レビューと @vivibui の承認要件は `.github/CONTRIBUTING.md` に従ってください。

## セキュリティと設定

認証情報やローカルの `.env` の値はコミットしないでください。付属の環境設定例を使用し、脆弱性は `SECURITY.md` の手順で報告してください。

## Agent skills

### Issue tracker

Issue と仕様は GitHub Issues（`gh` CLI）で管理する。詳細は `docs/agents/issue-tracker.md` を参照。

### Triage labels

デフォルトの 5 ラベル（`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`）をそのまま使用する。詳細は `docs/agents/triage-labels.md` を参照。

### Domain docs

single-context 構成（ルートの `GLOSSARY.md` と `docs/adr/`）。詳細は `docs/agents/domain.md` を参照。
