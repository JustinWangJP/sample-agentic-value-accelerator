# custom 以外の descriptor では、連絡先などと Skill の固定情報は補助テーブルを正本とする

A2A（`a2aAgentCard`）・MCP サーバー（`mcpServer`）・Skill（`agentSkillsDefinition`）の下書きでは、AWS が descriptor のスキーマで内容を検証する。そのため、custom descriptor のように連絡先・利用条件・接続先の参照を descriptor の中に入れられない。これらは補助テーブルの管理情報（`sk` = `MANAGEMENT`）に保存し、この種別では管理情報を正本とする。ADR-0008 の「descriptor を正本とし、補助テーブルは写し」は custom descriptor にだけ適用する。

Skill の固定した commit・version・digest も、同じ管理情報（`skillCommit`・`skillVersion`・`skillDigest`）に保存する。AWS の skill definition（スキーマ 0.1.0）には、これらの専用項目がないためである。リポジトリの URL と種類は、スキーマにある `repository` に入れる。

下書きの読み取りでは、descriptor の中身に加えて管理情報のこれらの項目を読んでフォームに戻す。管理情報はすでに内容ハッシュに含めているため、他の人が管理情報だけを変えていれば更新は 409 になる。読み取れない管理情報は、フォームで読めない下書きとして扱い（502）、上書きしない。

## 検討した選択肢

- Skill の commit・digest を descriptor の `_meta` に入れる案は採用しない。AWS は `_meta` をオブジェクトとしてしか定義しておらず、AWS が検証しない領域に配布物の固定情報を置くと、形式の保証はポータルが負うことになる。実環境で `_meta` が受理されることも確認できていない。実環境で受理されることを確認できれば、`_meta` に置いて Registry だけで版と配布物の対応を復元できるようにする案を再検討する。
- 連絡先などを Agent Card の `provider`・`documentationUrl` や server.json の項目に対応づける案は採用しない。項目の意味が一致せず、利用者が別の意味で入力した値と区別できなくなる。
- 連絡先などを custom metadata に置く案は、ADR-0007 のとおり採用しない。

## 影響

- 管理情報が壊れたり欠けたりすると、A2A・MCP・Skill の下書きは読み取れなくなる（custom Agent は descriptor から読める）。補助テーブルの復旧は、Registry の版だけでは連絡先と Skill の固定情報を復元できないことを前提に計画する。
- 公開承認（#10）は、これらの種別では管理情報から公開版スナップショット（ADR-0007）を作る。descriptor との一致確認は、descriptor に入っている項目（`repository` など）に限る。
- Skill の固定情報が Registry の descriptor だけでは分からないため、AWS コンソールや発見 API からは commit・version・digest が見えない。公開版スナップショットのカタログ詳細で全員に表示する。
