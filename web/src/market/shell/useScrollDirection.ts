import { useEffect, useState } from 'react'

export type ScrollDir = 'top' | 'up' | 'down'

/**
 * Passive, rAF-throttled scroll direction for the scroll-aware nav: 'top' near the page top,
 * 'down' after scrolling down more than `threshold`, 'up' after scrolling up more than it.
 * Never reports 'down' within 40px of the page bottom (the nav must be reachable at the end).
 */
export function useScrollDirection(threshold = 8, showAtTop = 24): ScrollDir {
  const [dir, setDir] = useState<ScrollDir>('top')
  useEffect(() => {
    let last = window.scrollY
    let ticking = false
    const onScroll = () => {
      if (ticking) return
      ticking = true
      window.requestAnimationFrame(() => {
        const y = window.scrollY
        const max = document.documentElement.scrollHeight - window.innerHeight
        const delta = y - last
        if (y <= showAtTop) setDir('top')
        else if (max - y < 40) setDir('up')
        else if (delta > threshold) setDir('down')
        else if (delta < -threshold) setDir('up')
        if (Math.abs(delta) > threshold) last = y
        ticking = false
      })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [threshold, showAtTop])
  return dir
}
