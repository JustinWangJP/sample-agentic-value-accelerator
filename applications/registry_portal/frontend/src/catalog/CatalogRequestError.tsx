export function CatalogRequestError({
  error,
  onRetry,
  onRelogin,
}: {
  error: { message: string; requiresLogin: boolean }
  onRetry: () => void
  onRelogin: () => void
}) {
  return (
    <div role="alert" className="error">
      <p>{error.message}</p>
      {error.requiresLogin ? (
        <button type="button" onClick={onRelogin}>
          もう一度ログインする
        </button>
      ) : (
        <button type="button" onClick={onRetry}>
          再試行
        </button>
      )}
    </div>
  )
}
