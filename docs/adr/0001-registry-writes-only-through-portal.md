# Registry への書き込みはポータル経由に限定する

申請内容の固定と自己承認の禁止は、ポータルの Lambda がすべての書き込みを仲介する場合にしか保証できない。AWS の決裁 API（`UpdateRegistryRecordStatus`）には、期待するリビジョンや If-Match を指定する引数がないためである。そこで、`CreateRegistryRecord`、`UpdateRegistryRecord`、`SubmitRegistryRecordForApproval`、`UpdateRegistryRecordStatus` の実行は、ポータルの Lambda 実行ロールと break-glass 用の管理ロールにだけ許可し、それ以外のプリンシパルには IAM で Deny する。CI や App Factory は、専用の M2M クライアント（サービス主体）でポータル API を呼び、下書きの作成と申請まで行う。

## Considered Options

- ポータル内での保証だけにとどめ、直接書き込みも許可する案は採用しない。コンソールや CI から直接承認すると、承認対象の内容と承認者の記録が一致しなくなるためである。

## Consequences

- break-glass ロールで行った直接変更は、EventBridge 経由で「ポータル外の変更」として Admin に通知し、監査の対象にする。
