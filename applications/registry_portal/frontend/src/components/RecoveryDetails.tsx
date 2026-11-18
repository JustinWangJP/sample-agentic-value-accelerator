import type { RecoveryMode, RecoveryRecord } from '../drafts/api'

const MODE_LABELS: Record<RecoveryMode, string> = {
  RECONCILED: 'Registry を再取得して確定',
  RELEASED: '確定できないまま強制解除',
}

/**
 * Admin の回復操作の結果（方法・主体・時刻・理由）。
 * 回復操作の画面、下書きと決裁の操作照会、操作と審査の履歴で同じ表示を使う
 */
export function RecoveryDetails({ recovery }: { recovery: RecoveryRecord }) {
  return (
    <>
      <p>回復の方法 {MODE_LABELS[recovery.mode]}</p>
      <p>
        回復した主体 {recovery.by.subject}（{recovery.by.issuer}）・{recovery.at}
      </p>
      <p>回復の理由 {recovery.reason}</p>
    </>
  )
}

/**
 * 操作照会で、Admin の回復操作が確定した操作を見つけたときの案内。
 * 回復結果を確認してから、各画面の結果の表示（onContinue）へ進む
 */
export function RecoveredOperationNotice({
  recovery,
  onContinue,
}: {
  recovery: RecoveryRecord
  onContinue: () => void
}) {
  return (
    <div role="status">
      <p>この操作は、Admin の回復操作で結果が確定しました。</p>
      <RecoveryDetails recovery={recovery} />
      <button type="button" onClick={onContinue}>
        結果を表示
      </button>
    </div>
  )
}
