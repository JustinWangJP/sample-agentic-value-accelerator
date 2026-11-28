# Registry ポータル UI 改善 静的 HTML プロトタイプ

`applications/registry_portal` のフロントエンド UI/UX 改善に向けた静的 HTML プロトタイプです。MVP1 の機能およびバックエンド API インターフェースは変更せず、今後の React（`applications/registry_portal/frontend/`）改修の設計図として利用します（Issue #83）。

設計判断の背景は [ADR-0016](../../../docs/adr/0016-registry-portal-frontend-tailwind-css.md)、用語は [GLOSSARY.md](../../../GLOSSARY.md)、レビュー基準は [CODING_STANDARDS.md](../CODING_STANDARDS.md) を参照してください。

## 閲覧方法

外部 CDN に依存せず、ビルド済み Tailwind CSS（`assets/styles.css`）を同梱しています。`index.html` をブラウザでそのまま開くか、ローカル HTTP サーバーで閲覧できます。

```bash
# ブラウザで直接開く（macOS）
open applications/registry_portal/ui-prototype/index.html

# またはローカルサーバーで開く
python3 -m http.server 8080 --directory applications/registry_portal/ui-prototype
```

各ページ最上部のプロトタイプ確認用バーで、次の 2 つを切り替えられます。選択はブラウザに保存され、ページを移動しても引き継がれます。

- **業務ロール切替**: サイドバー・モバイルメニューと、業務ロールで出し分ける操作の表示を切り替えます。
- **表示状態**: 「正常」「読み込み中」「0 件」「未取得」「準備中」「失敗 401」「失敗 403」「失敗 5xx」を切り替えます。各画面の一覧領域（カタログ・下書き・審査待ち・公開管理の検索結果・Registry 管理・通知）は、状態に応じたパネルに置き換わります。「未取得」は項目単位の表示（Registry 管理の接続情報、審査詳細の公開中の版のスナップショット）に、「準備中」は A2A 条件付きのカタログ検索結果に、「失敗 403」は審査詳細の自己承認の応答にも反映されます。

## 画面一覧

| ファイル | 画面 | 対象業務ロール |
|---|---|---|
| `index.html` | カタログ一覧・検索 | 全員（Viewer） |
| `catalog-detail.html` | カタログ詳細（Agent / Skill 表示切替・履歴ドロワー） | 全員（公開管理・履歴は Curator / Admin） |
| `drafts.html` | 下書き一覧 | Publisher |
| `draft-edit.html` | 下書き作成・編集（フォーム / JSON 切替・廃止依頼・履歴ドロワー） | Publisher |
| `submission.html` | 申請（関連参照・保存済み内容の確認） | Publisher |
| `reviews.html` | 審査待ち一覧（申請・廃止依頼） | Curator |
| `review-detail.html` | 審査詳細・決裁（差分テーブル・公開承認 / 却下・自己承認の 403） | Curator |
| `publication.html` | 公開管理（管理レコード検索・ID 照会・公開停止 / 廃止二重確認モーダル / 見送り） | Curator |
| `administration.html` | Registry 管理（接続情報・未管理レコードの取り込み・所有チーム変更） | Admin |
| `recovery.html` | 回復操作（結果確認中の照会・再取得照合・強制解除モーダル） | Admin |
| `states.html` | 状態・デザインシステム一覧（ログイン中・401/403/5xx・4 状態の区別・決裁できない申請の警告・状態バッジ） | 設計確認用 |

## ファイル構成

| パス | 内容 |
|---|---|
| `assets/prototype-model.js` | 業務ロール・ナビ・種別・descriptor・データ分類・schema 版・操作種別・通知種別の定義、サンプルデータ、DOM に依存しない判定関数 |
| `assets/prototype.js` | 共通シェル（確認用バー・ヘッダー・サイドバー・モバイルメニュー・履歴ドロワー・表示状態のパネル・選択肢の描画）と、ポップオーバー・ドロワー・モーダル・タブの動作 |
| `assets/prototype-pages.js` | 画面固有のデモ動作（カタログ検索の切替・descriptor 切替・再取得照合・二重確認） |
| `assets/styles.css` | ビルド済みの Tailwind CSS（`src/input.css` から生成。直接編集しない） |
| `tests/` | `node:test` によるテスト（追加の依存なし） |

3 つの JS は、各 HTML で `prototype-model.js` → `prototype.js` → `prototype-pages.js` の順に `defer` で読み込みます。ES モジュールは `file://` で直接開いたときに読み込めないため使いません。

### 本番フロントエンドの定義との関係

`CODING_STANDARDS.md` の「種別やデータ分類の値は一か所に定義する」に合わせ、プロトタイプでは選択肢やラベルを HTML に直接書かず、`assets/prototype-model.js` の定義から描画します（`<select data-options="...">`）。この定義は `frontend/src/` の次の定義の写しです。写しが本番とずれていないことは `tests/model.test.mjs` が照合します。

| プロトタイプの定義 | 本番の定義 |
|---|---|
| `RECORD_TYPES` | `frontend/src/api/recordType.ts` の `CATALOG_RECORD_TYPES`・`RECORD_TYPE_LABELS` |
| `DESCRIPTOR_TYPES`・`SCHEMA_VERSIONS` | `frontend/src/drafts/descriptors.ts` の `DESCRIPTOR_TYPES`・`DESCRIPTOR_LABELS`・`*_SCHEMA_VERSIONS` |
| `DATA_CLASSIFICATIONS` | `frontend/src/api/dataClassification.ts` の `DATA_CLASSIFICATIONS`・`DATA_CLASSIFICATION_LABELS` |
| `HISTORY_KIND_LABELS`・`HISTORY_STATE_LABELS` | `frontend/src/drafts/api.ts` の `KIND_LABELS`・`STATE_LABELS` |
| `NOTIFICATION_KIND_LABELS` | `frontend/src/notifications/api.ts` の `NOTIFICATION_KIND_LABELS` |

## 開発

依存は `package-lock.json` で固定しています。初回は `npm ci` で導入してください。

```bash
cd applications/registry_portal/ui-prototype
npm ci
npm test            # CSP 準拠・ローカルリンク・data-* 属性の値・用語・本番定義との一致・判定関数を検査
npm run build:css   # HTML・JS・src/input.css を編集したら assets/styles.css を再生成
```

### テストの範囲

`npm test` は Node の `node:test` だけで動き、次を検査します。

- **静的検査（`tests/static.test.mjs`）**: 11 画面の HTML と `assets/*.js` について、インライン `<style>`・`style=`・`on*=`・インライン `<script>`・外部リソースの読み込みがないこと（受け入れ条件の CSP 準拠）、スクリプトの読み込み順、ローカルリンクとアンカーの存在、`data-requires-roles`・`data-options`・`data-history-drawer`・`data-state-*` の値が定義済みであること、避ける用語を使っていないこと。
- **定義と判定関数（`tests/model.test.mjs`）**: 本番フロントエンドの定義との一致、業務ロールによる表示判定、表示状態の判定、廃止の二重確認の判定。

DOM の描画とクリック操作（ポップオーバーの開閉、ロール切替の表示結果など）は、jsdom などの追加依存を避けるため自動テストの対象外とし、ブラウザで目視確認します。React へ移植するときは、`frontend/` の Vitest でコンポーネントのテストとして書き直します。

## 本番 React への移植時の前提

- **CSP 準拠**: インライン `<style>`・インライン `<script>`・外部 CDN・外部フォントは使用していません（`style-src 'self'; script-src 'self'; font-src 'self'` 準拠）。
- **プロトタイプ専用の要素の除外**: 各ページ最上部のプロトタイプ確認用バー（業務ロール切替・表示状態切替・状態カタログへのリンク）と、`data-state-*` 属性による表示状態の重ね合わせは確認専用であり、本番 SPA には含めません。
- **4 状態の厳密な区別**: `CODING_STANDARDS.md` に従い、「0 件」「未取得」「準備中」「失敗（401 は再ログインあり / 403 は再ログイン・再試行なし / 5xx は再試行あり）」を区別しています。表示例の一覧は `states.html`、各画面での見え方は確認用バーの「表示状態」で確認できます。
- **自己承認**: 公開承認・却下のボタンは事前に無効化せず、決裁の送信後に返る 403 のメッセージを表示します（現行 SPA と同じ挙動）。
- **共通部品の単位**: 共通シェル（`prototype.js`）と画面固有の動作（`prototype-pages.js`）の分け方を、React のレイアウト部品と画面部品の分割の目安にします。履歴ドロワーと状態パネルは、それぞれ 1 つの共通コンポーネントにします。
