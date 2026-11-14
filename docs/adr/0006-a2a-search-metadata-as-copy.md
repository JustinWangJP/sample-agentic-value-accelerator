# A2A の判定は descriptor を正本とし、検索には isA2A の写しを使う

A2A かどうかは、Agent の descriptor に Agent Card（`a2aAgentCard`）があるかで決める。一覧では、AWS の一覧フィルター `descriptorType` で絞り込める。しかし AWS の検索フィルターに使えるのは `name`・`recordType`・`recordVersion`・`customMetadata.*` だけで、descriptor の種類は条件にできない。結果を後から絞り込むと、上位20件の外にある該当項目を取りこぼす。そこで、descriptor から導出した真偽値を custom metadata の `isA2A` に検索用の写しとして保存し、順位付けの前に AWS のフィルターで絞り込む。

## 検討した選択肢

- 検索結果を受け取ってから descriptor で絞り込む案は採用しない。上位20件の外にある該当項目が結果に出ないためである。
- `isA2A` を利用者の入力にする案も採用しない。descriptor と食い違う値が登録され得るためである。

## 影響

- `isA2A` がないレコードは AWS のフィルターで黙って除外され、0件と区別できない。そのため A2A の絞り込み検索は `A2A_SEARCH_ENABLED` で有効にするまで受け付けず、503 `A2A_SEARCH_NOT_READY` を返す。既存レコードを補完して確認できたら有効にする。
- 写しと descriptor が食い違うレコードは、descriptor を正として検索結果から除外し、件数を利用者に示す。検索全体は失敗させない。
- 作成・更新では descriptor から `isA2A` を導出して保存する（#7・#8）。既存レコードは、取り込み（#16）で補完の要否（`a2aBackfillRequired`）を判定し、所有チームの通常の編集・申請・公開承認で補完する。補完が必要なことは、取り込みの応答に加え、所有チームが開く取り込みレコードの編集画面にも保存で補完されるまで表示する。取り込みを受け持つ決裁 Lambda は descriptor・metadata を更新する権限を持たない（設計書「最小インフラとセキュリティ」）ため、取り込み自体は Registry を更新しない。ポータル外での補完は [ADR-0001](0001-registry-writes-only-through-portal.md) の break-glass ロールに限る。
- Registry の custom metadata schema への `isA2A` の追加は、Admin が IaC で行う。この IaC をどこに置くかは未決定である。

運用手順は [README の「A2A 検索を有効にする」](../../applications/registry_portal/README.md#a2a-検索を有効にする) を参照する。schema を管理する IaC の配置は [親 Issue #1](https://github.com/JustinWangJP/sample-agentic-value-accelerator/issues/1) で判断する。
