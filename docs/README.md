# AVA 日本語開発者ドキュメント

- [AWS構成図（draw.io・5ページ）](diagrams/ava-aws-architecture-ja.drawio)：VPC、Subnet、CIDR、経路、外部SaaS・任意連携。前提・編集方法は[図のREADME](diagrams/README.md)を参照。

- [技術設計・保守ガイド](technical-guide-ja.html)：AWS・アプリ構成図、データ・認証、主要23機能の実装対応表、継続開発・運用・復旧手順。
- [オンボーディング入門](onboarding-ja.html)：Repoの読み方、初回起動、基本的な処理の流れ。
- [プリンシパル向けオンボーディングガイド](onboarding/principal-guide-ja.md)：設計思想、判断ログ、データフロー、障害モード、セキュリティ、技術的負債（Markdown・Mermaid図）。
- [ゼロからはじめるコントリビューターガイド](onboarding/contributor-guide-ja.md)：環境構築、最初のタスク、テスト、デバッグ、コードパターン、クイックリファレンス（Markdown）。
- 中国語（簡体字）版：[资深工程师入职指南](onboarding/principal-guide-zh.md) ／ [从零开始的贡献者指南](onboarding/contributor-guide-zh.md)（日本語版と同じ構成・同じ根拠）。

HTMLをブラウザで開いて閲覧できます。外部ライブラリ・通信は不要です。ソースへのリンクを使う場合は、docs単体ではなくリポジトリと一緒に保持してください。印刷メニューからPDFとして保存できます。

解析基準は2026-10-31、コミット `2b8849c` と解析時の作業ツリーです。実装確認と推奨運用・受入確認案を区別しています。AWS実環境への配備やアプリテストの成功を示す資料ではありません。

API、インフラ、データ、配備手順を変更した場合は、ガイド内の該当表・構成図・根拠リンクも更新してください。
