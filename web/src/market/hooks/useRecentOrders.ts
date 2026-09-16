import { useEffect, useState } from 'react'
import type { OrderStatusPayload } from '@/lib/shopApi'
import { rememberedOrders } from '../lib/device'
import { getOrder } from '../lib/marketApi'

/** Full details of the newest `n` orders this device placed (session-cached; drives Order again / Regular stock). */
const cache = new Map<string, Promise<OrderStatusPayload | null>>()

export function fetchOrderCached(token: string): Promise<OrderStatusPayload | null> {
  let p = cache.get(token)
  if (!p) {
    p = getOrder(token).catch(() => null)
    cache.set(token, p)
  }
  return p
}

export function useRecentOrders(enabled: boolean, n = 3): OrderStatusPayload[] {
  const [orders, setOrders] = useState<OrderStatusPayload[]>([])
  useEffect(() => {
    if (!enabled) return
    const tokens = rememberedOrders()
      .slice(0, n)
      .map((o) => o.token)
    if (!tokens.length) return
    let alive = true
    Promise.all(tokens.map(fetchOrderCached)).then((rs) => alive && setOrders(rs.filter((r): r is OrderStatusPayload => Boolean(r))))
    return () => {
      alive = false
    }
  }, [enabled, n])
  return orders
}
