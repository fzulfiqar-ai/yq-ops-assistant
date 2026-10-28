import { Suspense, useEffect, useState, type ReactNode } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { PageSkeleton } from '../ui/Skeleton'

/**
 * The page slot of both shells (28-Oct-2026 polish: harness check S5 and the page glide).
 *
 * Its own Suspense boundary, keyed by page. A page whose code is still downloading shows a skeleton
 * INSIDE the shell, with the header and nav in place; before, the nearest boundary sat above the
 * shell, so a direct open was a blank screen. And because each page gets a fresh boundary, a tap
 * shows the skeleton at once instead of freezing the old page until the code arrives. The page then
 * glides in (.m-page-in: a fade and an 8 px rise, 200 ms). The first page of a visit does not, so
 * nothing holds back the first paint.
 *
 * Home, a product deep link (/p/{code}) and a rep storefront (/{slug}) share one key: they are the
 * same page, and the panel opening or closing must never remount it.
 */
const PAGES = new Set(['t', 'shop', 'search', 'quick', 'about', 'brands', 'cart', 'checkout', 'orders', 'me', 'o'])

// eslint-disable-next-line react-refresh/only-export-components
export function pageKey(pathname: string): string {
  const [first = '', second = ''] = pathname.split('/').filter(Boolean)
  if (!PAGES.has(first)) return 'home'
  return first === 't' || first === 'brands' || first === 'o' ? `${first}/${second}` : first
}

let landed = false // the first page of the visit has painted: later pages glide in

/** Decides once, when the page mounts, so a filter or a panel on the same page never replays it. */
function Glide({ children }: { children: ReactNode }) {
  const [glide] = useState(() => landed)
  useEffect(() => {
    landed = true
  }, [])
  return <div className={glide ? 'm-page-in' : undefined}>{children}</div>
}

export function RouteOutlet() {
  const { pathname } = useLocation()
  const key = pageKey(pathname)
  return (
    <Suspense key={key} fallback={<PageSkeleton />}>
      <Glide>
        <Outlet />
      </Glide>
    </Suspense>
  )
}
