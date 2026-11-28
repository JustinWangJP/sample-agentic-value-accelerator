import { useNavigation } from '../routing/navigation'
import { canOpenScreen } from '../routing/routes'
import { ReviewDetailLoader } from './ReviewDetail'
import { ReviewList } from './ReviewList'
import type { ScreenProps } from '../shell/screenProps'

type Props = ScreenProps

/** Curator の審査待ちの一覧と、申請のスナップショットに基づく公開承認・却下、廃止依頼への対応 */
export function ReviewsView({ api, roles, onRelogin }: Props) {
  const { route, navigate } = useNavigation()
  const recordId = route.screen === 'reviews' ? route.recordId : null
  if (!canOpenScreen('reviews', roles)) return null
  if (recordId !== null)
    return (
      <ReviewDetailLoader
        key={recordId}
        api={api}
        recordId={recordId}
        onBack={() => navigate({ screen: 'reviews', recordId: null })}
        onRelogin={onRelogin}
      />
    )
  return (
    <ReviewList api={api} onOpen={(id) => navigate({ screen: 'reviews', recordId: id })} onRelogin={onRelogin} />
  )
}
