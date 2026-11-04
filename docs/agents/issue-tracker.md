# Issue tracker: GitHub

このリポジトリの Issue と仕様は GitHub Issues で管理する。操作はすべて `gh` CLI で行う。

## 操作規約

- **Issue の作成**: `gh issue create --title "..." --body "..."`。複数行の本文はヒアドキュメント（heredoc）を使う。
- **Issue の参照**: `gh issue view <number> --json number,title,body,labels,comments`。
- **Issue の一覧**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`。必要に応じて `--label` と `--state` で絞り込む。
- **親 Issue への sub-issue 登録**: `gh issue create --parent <parent> ...`、または作成後に `gh issue edit <parent> --add-sub-issue <child>`（`gh` 2.94 以降）。古い `gh` では `gh api --method POST repos/<owner>/<repo>/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`（database id。下記 **Blocking** を参照）。sub-issue が使えない場合は、子 Issue 本文の先頭に `Part of #<parent>` と記載する。
- **コメント**: `gh issue comment <number> --body "..."`
- **ラベルの付与・削除**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **クローズ**: `gh issue close <number> --comment "..."`

対象リポジトリは `git remote -v` から推定する（クローン内で実行すれば `gh` が自動判定する）。

## Pull Request を triage 対象とするか

**PRs as a request surface: no.** _（外部からの PR を機能要望として扱う場合は `yes` に変更する。`/triage` がこのフラグを参照する。）_

`yes` の場合、PR も Issue と同じラベル・状態で扱い、`gh pr` の対応コマンドを使う。

- **PR の参照**: `gh pr view <number> --comments`、差分は `gh pr diff <number>`。
- **triage 対象の外部 PR 一覧**: `gh api --paginate 'repos/{owner}/{repo}/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`。
- **コメント・ラベル・クローズ**: `gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`。

GitHub では Issue と PR が同じ番号空間を共有するため、`#42` だけではどちらか判別できない。`gh pr view 42` で解決を試み、失敗したら `gh issue view 42` にフォールバックする。

## スキルが「issue tracker に公開する（publish to the issue tracker）」と言った場合

GitHub Issue を作成する。

## スキルが「関連チケットを取得する（fetch the relevant ticket）」と言った場合

上記 **Issue の参照** の手順で読む。

## Wayfinding 操作

`/wayfinder` が使用する。**map** は 1 つの Issue で、その **child** Issue がチケットになる。

- **Map**: `wayfinder:map` ラベルを付けた単一の Issue。本文に Notes / Decisions-so-far / Fog を持つ。`gh issue create --label wayfinder:map`。
- **Child ticket**: map に GitHub sub-issue として紐付けた Issue（**親 Issue への sub-issue 登録** を参照）。sub-issue が使えない場合は、map 本文のタスクリストに追加し、子 Issue 本文の先頭に `Part of #<map>` と記載する。ラベルは `wayfinder:<type>`（`research`/`prototype`/`grilling`/`task`）。着手時に担当開発者をアサインする。
- **Blocking**: GitHub の**ネイティブな Issue 依存関係（issue dependencies）**を正式な表現とする（UI 上でも確認可能）。`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>` で依存関係を追加する。`<blocker-db-id>` はブロッカーの数値 **database id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`。`#number` や `node_id` ではない）。GitHub は `issue_dependencies_summary.blocked_by`（未クローズのブロッカーのみ）を返し、これが実際の着手可否の判定になる。依存関係機能が使えない場合は、子 Issue 本文の先頭に `Blocked by: #<n>, #<n>` 行を書く。すべてのブロッカーがクローズされたらブロック解除とみなす。
- **Frontier クエリ**: map の未クローズの子 Issue を列挙し（`gh issue list --state open`、map の sub-issue またはタスクリストに限定）、未クローズのブロッカーを持つもの（`issue_dependencies_summary.blocked_by > 0`、または `Blocked by` 行に未クローズの Issue があるもの）とアサイン済みのものを除外する。map 上の順序で先頭のものを選ぶ。
- **Claim**: `gh issue edit <n> --add-assignee @me`。セッションで最初に行う書き込み操作とする。
- **Resolve**: `gh issue comment <n> --body "<answer>"` の後に `gh issue close <n>` を実行し、map の Decisions-so-far に要旨とリンクを追記する。
