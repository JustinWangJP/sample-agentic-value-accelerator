# 結果確認中の操作の回復は、Admin が決裁 handler で行い、Registry の再取得で確定できないときだけ強制解除する

設計書「内容を確認してから決裁する」は、結果確認中の操作を Registry の再取得で確定するまで、そのレコードへの書き込みを止めることを定めている。ADR-0010 は決裁の確定の経路を決裁 handler の再送と、決裁した Curator の操作照会に限った。この2つで確定できない操作（決裁した Curator や下書きの Publisher が不在、補助ストア障害が続く、AWS の結果が観測できない）が残ると、ロックは期限で解除しないため、そのレコードへの書き込みが止まり続ける。この回復を Admin の回復操作として、次のとおりに定める。

- **入口**: 決裁 handler の `POST /api/records/{recordId}/recovery`（Admin だけ）。状態遷移の権限（`UpdateRegistryRecordStatus`）を持つのは決裁 Lambda だけで、回復も AWS の呼び出し結果を観測する側に置く。対象の操作はレコード共通のロックを持つ操作から特定し、操作 ID を利用者から受け取らない（結果確認中の決裁の操作 ID は、決裁した Curator 以外に返さないため。ADR-0010）。`GET` と回復の応答は、操作 ID を含めず、ロックを持つ操作の種別・主体・開始時刻・回復できるようになる時刻と回復できるか、回復の結果だけを返し、操作の中身（理由・ハッシュ）も返さない。
- **照合**: Registry を再取得し、観測できる状態だけから操作を確定する。下書きの操作（作成・更新・申請）も決裁の操作も同じ規則で確定し、AWS の書き込み API（申請 API・状態更新 API）は呼び直さない。
- **強制解除**: 照合で確定できず、Admin が `release` を指定したときだけ、Registry を再取得でき、AWS が書き込みを処理中（`CREATING`・`UPDATING`）でないことを確かめたうえで、操作を失敗（`ADMIN_RELEASED`）として確定しロックを解除する。Registry を読めなければ解除せず（502）、処理中なら解除しない（409 `RECORD_STILL_PROCESSING`）。AWS が受け付け（成功応答）を返したと記録済みの決裁・申請（`decisionAccepted`・`submissionAccepted`）も解除しない（409 `ACCEPTED_BY_REGISTRY`）。AWS に届いた書き込みを失敗と記録すると、反映後の Registry と記録が食い違うためである。決裁の受け付けは、照合の前に操作へ保存する。
- **記録**: 回復した主体・時刻・理由・方法（`RECONCILED` / `RELEASED`）を、確定した操作と操作記録（RESULT）に残す。操作照会と履歴には回復結果を載せ、強制解除された操作の失敗の message には強制解除と理由を含める。
- **実行中の操作との競合**: 開始から5分が過ぎていない操作は回復しない。Lambda のタイムアウト（30秒）と再試行を超える時間であり、実行中の処理に重ねて確定・解除しないためである。ロックを期限で解除しない原則は変えない。

## 検討した選択肢

- 操作照会（`GET /api/operations/{id}`）に Admin を加える案は採用しない。操作の中身を主体以外に渡さない ADR-0010 の境界が崩れ、管理 Lambda に決裁の権限が必要になる。
- 回復を、操作 ID を受け取る API にする案は採用しない。Admin が操作 ID を知る手段がなく、結果確認中の決裁の操作 ID を Admin に渡すと ADR-0010 の境界が崩れる。
- 照合を省き、Admin の判断だけで解除する案は採用しない。AWS に届いていた書き込みの上から別の書き込みを許し、誤った成功・失敗の記録につながる。
- 強制解除のときに、Admin の指定した結果（成功・失敗）をそのまま記録する案は採用しない。観測できない結果を、ポータルが成功として記録することになる。強制解除は失敗として確定し、Registry の実際の状態は、その後の照合と、Registry の状態を踏まえた再実行で扱う。
- 申請 API や状態更新 API を、Admin の回復で送り直す案は採用しない。決裁 Lambda は申請の権限を持たず、送り直しは決裁した Curator の再送（ADR-0010）に限るため。

## 影響

- レコードの ID が決まる前に失敗した作成（Registry の応答が届かなかった作成）は、レコードのロックを持たないため、この回復では扱えない。同じ入力と Idempotency-Key での再試行（AWS の `clientToken`）で確定する。
- 強制解除した操作が実際には AWS に反映されていた場合、Registry の状態とポータルの記録（公開版スナップショットなど）が食い違う。Admin は強制解除の前に Registry の状態を確認し、食い違いが出たときは、その状態を踏まえた再実行（所有チームの再申請と Curator の再決裁）で整える。取り込み（#16）は未管理レコードだけを対象とし、管理下のレコードの管理情報と公開版スナップショットは書き換えない（409）。申請の semver の下限は、補助ストアの版と発見 API が返す公開承認済みの版の大きい方とするため、記録が古くても公開中の版より古い版は申請できない。
- 回復の画面と API は Admin だけに開く。Admin が決裁の自己承認禁止から除外されないのと同じく、回復の理由と主体が操作記録に残る。
- 開始から5分の判定は操作の開始時刻を基準にする。同時に確定した操作は、ロックを削除する条件付き書き込み（操作 ID の一致）が1回しか成功しないため、先に確定した結果を後から上書きしない。ただし実行中の Lambda が、回復の後に AWS を呼ぶ可能性は残るため、強制解除の前に Registry の状態を確認する。
- `clientToken` を持つ Registry 管理 API は作成（[`CreateRegistryRecord`](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/create_registry_record.html)）だけで、[更新](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/update_registry_record.html)・[申請](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/submit_registry_record_for_approval.html)・[状態更新](https://docs.aws.amazon.com/boto3/latest/reference/services/agent-registry-control/client/update_registry_record_status.html)にはない（公式ドキュメントと botocore のサービスモデルで確認）。作成は同じ操作 ID を `clientToken` に使って再試行し、他は照合で確定する。
