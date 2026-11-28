# Registry ポータル UI 改善 静的 HTML プロトタイプ

`applications/registry_portal` のフロントエンド UI/UX 改善に向けた静的 HTML プロトタイプです。MVP1 の機能およびバックエンド API インターフェースは変更せず、今後の React（`applications/registry_portal/frontend/`）改修の設計図として利用します。

設計判断の背景は [ADR-0016](../../../docs/adr/0016-registry-portal-frontend-tailwind-css.md)、用語は [GLOSSARY.md](../../../GLOSSARY.md) を参照してください。

## 閲覧方法

外部 CDN に依存せず、ビルド済み Tailwind CSS（`assets/styles.css`）を同梱しています。`index.html` をブラウザでそのまま開くか、ローカル HTTP サーバーで閲覧できます。

```bash
# ブラウザで直接開く（macOS）
open applications/registry_portal/ui-prototype/index.html

# またはローカルサーバーで開く
python3 -m http.server 8080 --directory applications/registry_portal/ui-prototype
```

## 画面一覧

| ファイル | 画面 | 対象業務ロール |
|---|---|---|
| `index.html` | カタログ一覧・検索 | 全員（Viewer） |
| `catalog-detail.html` | カタログ詳細（Agent / Skill 表示切替・履歴ドロワー） | 全員（公開管理・履歴は Curator / Admin） |
| `drafts.html` | 下書き一覧 | Publisher |
| `draft-edit.html` | 下書き作成・編集（フォーム / JSON 切替・廃止依頼・履歴ドロワー） | Publisher |
| `submission.html` | 申請（関連参照・保存済み内容の確認） | Publisher |
| `reviews.html` | 審査待ち一覧（申請・廃止依頼） | Curator |
| `review-detail.html` | 審査詳細・決裁（差分テーブル・警告・公開承認 / 却下） | Curator |
| `publication.html` | 公開管理（管理レコード検索・ID 照会・公開停止 / 廃止二重確認モーダル / 見送り） | Curator |
| `administration.html` | Registry 管理（接続情報・未管理レコードの取り込み・所有チーム変更） | Admin |
| `recovery.html` | 回復操作（結果確認中の照会・再取得照合・強制解除モーダル） | Admin |
| `states.html` | 状態・デザインシステム一覧（ログイン中・401/403/5xx・4状態の区別・結果確認中・各種警告） | 設計確認用 |

## Tailwind CSS の再ビルド

HTML や `src/input.css` を編集した場合は、以下で `assets/styles.css` を再生成してください。

```bash
cd applications/registry_portal/ui-prototype
npm install
npm run build:css
```

## 本番 React への移植時の前提

- **CSP 準拠**: インライン `<style>`・インライン `<script>`・外部 CDN・外部フォントは使用していません（`style-src 'self'; script-src 'self'; font-src 'self'` 準拠）。
- **プロトタイプ専用バーの除外**: 各ページ最上部の「プロトタイプ確認用バー（業務ロール切替・状態カタログリンク）」は確認専用であり、本番 SPA には含めません。
- **4 状態の厳密な区別**: `CODING_STANDARDS.md` に従い、「0 件」「未取得」「準備中」「失敗（401 再ログインあり / 403・5xx 再ログインなし）」を明確に区別しています（`states.html` 参照）。
