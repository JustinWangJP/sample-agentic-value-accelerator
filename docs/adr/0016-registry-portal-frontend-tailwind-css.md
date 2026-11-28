# Registry ポータルのフロントエンドに Tailwind CSS（静的ビルド配信）を採用する

Registry ポータルのフロントエンド（`applications/registry_portal/frontend/`）の UI 改善および今後の画面実装において、スタイリング基盤に Tailwind CSS（v4）を採用し、ビルド時に単一の静的 CSS ファイルへ出力して配信する。MVP1 の機能およびバックエンド API のインターフェースは変更しない。

現行のフロントエンドは外部 UI ライブラリを持たず、単一の `index.css` による素の CSS で構成されていた。UI/UX 改善により左サイドバー・ヘッダー・通知パネル・履歴ドロワー・差分テーブル・確認モーダルなど画面構成と状態表現が増えるにあたり、一貫したデザイントークン（色・余白・タイポグラフィ・状態バッジ）を保ちながら、静的 HTML プロトタイプ（`applications/registry_portal/ui-prototype/`）から React コンポーネントへそのままスタイルを移植できる基盤が必要になった。

本番の CloudFront 配信では厳格な CSP（`style-src 'self'; script-src 'self'; font-src 'self'`、`'unsafe-inline'` なし）を適用している。Tailwind CSS は実行時ランタイムやインライン `<style>` 注入を必要とせず、Vite / CLI のビルド時に静的 CSS ファイルを生成するため、既存の CSP 制約を緩めることなく導入できる。

## 検討した選択肢

- **CSS カスタムプロパティ＋素の CSS を拡張する案**: 依存を増やさずに済むが、6 つの機能領域と多数の状態バリエーション（0件・未取得・準備中・失敗、結果確認中、各種警告など） across で命名規則と余白・色の一貫性を保つ保守コストが高くなるため採用しない。
- **重量級のコンポーネント UI ライブラリ（MUI / Cloudscape Design System 等）を導入する案**: 依存サイズと学習コストが大きく、一部ライブラリは実行時の CSS-in-JS や動的スタイル注入により厳格な CSP（`style-src 'self'`）と競合するおそれがあるため採用しない。
- **Tailwind Play CDN を利用する案**: 外部 CDN 読み込みと実行時スタイル生成を行うため、本番の CSP とオフライン閲覧要件を満たさない。プロトタイプ段階からローカルビルドした静的 CSS を用いる。

## 影響

- まず `applications/registry_portal/ui-prototype/` に Tailwind CSS（ローカルビルド済み `assets/styles.css`）を用いた静的 HTML プロトタイプを作成し、レイアウト・情報設計・状態表示を合意してから React 実装へ移植する。
- React 側（`applications/registry_portal/frontend/`）への移植時は、`tailwindcss` と `@tailwindcss/vite` を `devDependencies` に追加し、本番ビルド（`npm run build`）で静的 CSS バンドルとして出力する。既存の CSP 設定（`infrastructure/spa.tf`、`src/connectCsp.ts`）は変更しない。
- フォントは引き続きシステムフォント（`font-src 'self'` 準拠）を使用し、外部フォントは読み込まない。
