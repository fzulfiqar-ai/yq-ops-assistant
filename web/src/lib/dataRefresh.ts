/**
 * The one list of queries a data change makes stale — a Focus upload, a batch commit or its undo, a removed
 * day, or a goods-receipt (MRN) upload that moves the landed costs.
 *
 * The portal keeps a page's answer for five minutes (PortalRoot.tsx: business data only changes when reports
 * are uploaded). Those actions used to refresh only the Data page's own coverage and batch lists, so the
 * reports, the header's freshness chip and the Command Centre went on showing the old figures for up to five
 * minutes after a load. Every key below is matched as a PREFIX (react-query's default), so ['report'] covers
 * the Dashboard, Sales, Inventory, Profitability and Receivables at once.
 *
 * Only the queries a page has on screen are read again at once; the others are marked stale and read on their
 * next visit — so a generous list costs nothing. The live marketplace (orders, the shop's own settings) is not
 * Focus data and is never on it.
 */
export const DATA_QUERY_KEYS: readonly (readonly string[])[] = [
  ['report'],                 // /report/* — Dashboard, Sales, Inventory, Profitability, Receivables
  ['freshness'],              // the header chip: "Focus data to …"
  ['management'],             // the Command Centre's overview
  ['command'],                // the Command Centre's team message
  ['stock-daily'], ['stock-flow'], ['stock-transfers'], ['stock-recon'],   // Stock moves (the stock ledger)
  ['price-tracker'], ['price-history'],                                    // the price books and the costs
  ['catalog'],                // the catalog editor reads the price book and the stock snapshot
  ['shop-attainment'], ['shop-statements'],                                // rep targets and statements (Focus sales)
  ['shop-margins'], ['shop-unpriced-stock'],                               // Offers & rules: costs, price book, stock
  ['data', 'coverage'], ['ingest', 'batches'],                             // the Data page itself
]

/** Does a refresh after a data change reach this query (prefix match, as react-query invalidates)? */
export function coversKey(key: readonly unknown[]): boolean {
  return DATA_QUERY_KEYS.some((k) => k.length <= key.length && k.every((part, i) => key[i] === part))
}

/** The minimum of a QueryClient this needs (a test passes a stand-in). */
export interface Invalidator {
  invalidateQueries: (filters: { queryKey: readonly unknown[] }) => unknown
}

/** Read again every query a data change makes stale; resolves when the pages on screen have their new answer. */
export async function refreshAfterDataChange(qc: Invalidator): Promise<void> {
  await Promise.all(DATA_QUERY_KEYS.map((queryKey) => qc.invalidateQueries({ queryKey: [...queryKey] })))
}
