# Registry を正本とし、DynamoDB は補助に限定する

Registry レコードの定義と状態（DRAFT / PENDING_APPROVAL / APPROVED / REJECTED / DEPRECATED）は、AWS Agent Registry だけを正本とする。独自の承認状態は持たない。補助の DynamoDB テーブル（1つ）には、Registry に保存できない情報だけを置く。具体的には、申請のスナップショットとハッシュ、人間の申請者・決裁者、操作履歴、冪等性・排他制御、所有チームと関連参照、通知である。既存の Control Plane は、独自の承認キューが AWS の状態と食い違う経路を持っていた。本決定はその再発を防ぐためのものである。

## Considered Options

- Registry の標準承認だけを使い、補助ストアを持たない案は採用しない。人間の申請者と決裁者の対応付け、審査対象の固定、同時操作の制御ができないためである。
- 既存 Control Plane の汎用承認キューを継承する案も採用しない。

## Consequences

- AWS と DynamoDB の間に分散トランザクションはない。AWS では成功したが補助情報の保存に失敗した場合や、結果が不明な場合は「結果確認中」として記録する。その後 Registry を再取得して照合し、確認できるまでそのレコードへの新たな書き込みを止める。
