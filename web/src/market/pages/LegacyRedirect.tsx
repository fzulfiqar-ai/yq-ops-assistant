import { useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { isSlugShaped } from '../lib/device'
import { setSource } from '../lib/events'

/**
 * /c/{token} links already live in WhatsApp threads. On the marketplace host they land here
 * and are sent to the storefront: ?ref=ahmed → /ahmed, ?item=X05 → /p/X05, ?src kept for
 * attribution. The token itself is not needed — the marketplace resolves it server-side.
 */
export default function LegacyRedirect() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  useEffect(() => {
    const ref = (params.get('ref') || '').toLowerCase()
    const item = params.get('item') || ''
    setSource(params.get('src'))
    const qs = ref && isSlugShaped(ref) && item ? `?ref=${encodeURIComponent(ref)}` : ''
    if (item) navigate(`/p/${encodeURIComponent(item)}${qs}`, { replace: true })
    else if (ref && isSlugShaped(ref)) navigate(`/${ref}`, { replace: true })
    else navigate('/', { replace: true })
  }, [params, navigate])
  return null
}
