import { useMemo } from 'react'
import { useMarket } from '../MarketContext'
import { locale, S } from '../strings'
import { bhd, fmtDateShort } from './format'
import type { Words } from './serverWords'

/** The page's words for the server's sentences (lib/serverWords.ts): its strings, language, money
 *  and — when the stock snapshot is stale — the date a "Sold Out" line is measured against. */
export function useServerWords(): Words {
  const { stockStale, stockAsOf } = useMarket()
  return useMemo(() => ({ t: S, lang: locale.lang, bhd: (n: number) => bhd(n), asOf: stockStale ? fmtDateShort(stockAsOf) : null }), [stockStale, stockAsOf])
}
