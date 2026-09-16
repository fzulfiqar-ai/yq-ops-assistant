import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronRight, Download, Globe, Heart, Info, MessageCircle, Package, Phone, RotateCcw, Trash2, UserRound, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'
import { MarketCard } from '../components/MarketCard'
import { Rail } from '../components/Rail'
import { RepCard } from '../components/RepCard'
import { useRecentOrders } from '../hooks/useRecentOrders'
import { bestSellers, regularStock } from '../lib/home'
import { useSaved } from '../store/saved'
import { useMarket, useOrder } from '../MarketContext'
import { readCustomer, rememberedOrders, setSaveDetails, writeCustomer, type CustomerDraft } from '../lib/device'
import { track } from '../lib/events'
import { bhd, fmtDate, initials } from '../lib/format'
import { canPromptInstall, isIos, isStandalone, onInstallChange, promptInstall } from '../lib/install'
import { usePageTitle } from '../shell/ShellContext'
import { S } from '../strings'
import { AnchorButton, Button } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { Input, Label } from '../ui/Field'
import { useToast } from '../ui/Toast'

declare const __BUILD_ID__: string

/**
 * /me — "YQ knows my business": the rep, quick actions, recent orders, the details this phone
 * remembers (and how to forget them), install, language, contact. No account required.
 */
export default function MyYQPage() {
  const navigate = useNavigate()
  const { rep, data, recognized } = useMarket()
  const mkt = useMarket()
  const recentOrders = useRecentOrders(mkt.recognized, 3)
  const regulars = useMemo(() => regularStock(recentOrders, mkt.itemsByCode, new Set()), [recentOrders, mkt.itemsByCode])
  const best = useMemo(() => bestSellers(mkt.items), [mkt.items])
  const savedCodes = useSaved()
  const savedItems = useMemo(() => savedCodes.map((c) => mkt.itemsByCode.get(c)).filter((x): x is NonNullable<typeof x> => Boolean(x)), [savedCodes, mkt.itemsByCode])
  const { myOrders } = useOrder()
  const toast = useToast()
  const [customer, setCustomer] = useState<CustomerDraft>(() => readCustomer())
  const [editing, setEditing] = useState(false)
  const [installable, setInstallable] = useState(() => canPromptInstall())
  usePageTitle(S.me.title, false, `${S.me.title} · ${S.brand}`)
  useEffect(() => onInstallChange(() => setInstallable(canPromptInstall())), [])
  const hasOrders = rememberedOrders().length > 0
  const open = myOrders.find((o) => o.status !== 'delivered' && o.status !== 'cancelled') || null

  const saveDetails = () => {
    writeCustomer(customer)
    setEditing(false)
  }
  const forget = () => {
    setSaveDetails(false)
    setCustomer({ name: '', phone: '', shop: '', area: '', email: '' })
    toast(S.me.forgotten, 'info')
  }
  const clearAll = () => {
    if (!window.confirm(S.me.clearConfirm)) return
    try {
      for (const k of ['yq-orders', 'yq-qty', 'yq-shop-customer', 'yq-searches', 'yq-viewed', 'yq-cart-note', 'yq-lists', 'yq-view']) localStorage.removeItem(k)
    } catch {
      /* ignore */
    }
    toast(S.me.cleared, 'info')
    navigate('/')
  }
  const install = async () => {
    const r = await promptInstall()
    if (r === 'accepted') track('install')
    setInstallable(canPromptInstall())
  }

  const row = 'flex items-center gap-3 px-4 py-3.5 text-sm font-semibold text-ink hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70'

  return (
    <div className="px-gutter lg:px-0">
      <div className="mx-auto max-w-2xl lg:mx-0 lg:grid lg:max-w-none lg:grid-cols-2 lg:gap-6">
        <div>
          {/* greeting + rep */}
          <section className="rounded-xl border border-line bg-surface p-4">
            <div className="flex items-center gap-3">
              <span className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-plum-soft font-display text-base font-bold text-plum-ink">{customer.shop || customer.name ? initials(customer.shop || customer.name) : <UserRound size={22} />}</span>
              <div className="min-w-0">
                <div className="text-xs text-ink-2">{recognized ? S.me.hello : S.me.hi}</div>
                <div className="truncate font-display text-lg font-bold text-ink">{customer.shop || customer.name || S.brand}</div>
              </div>
            </div>
            <div className="mt-4">
              {rep ? (
                <RepCard rep={rep} />
              ) : (
                <div className="rounded-lg border border-dashed border-line bg-canvas px-4 py-3 text-sm text-ink-2">
                  <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-plum">{S.rep.yours}</div>
                  <div className="mt-0.5">{S.rep.soon}</div>
                </div>
              )}
            </div>
          </section>

          {/* quick actions */}
          <section className="mt-4 grid grid-cols-3 gap-2" aria-label={S.me.quick}>
            {[
              { to: '/quick?load=last', label: S.rails.regulars, icon: RotateCcw, hide: !hasOrders },
              { to: '/quick', label: S.nav.quick, icon: Zap },
              { to: open ? `/o/${open.token}` : '/orders', label: S.me.track, icon: Package },
            ]
              .filter((a) => !a.hide)
              .map((a) => (
                <Link key={a.label} to={a.to} className="flex flex-col items-center gap-1.5 rounded-lg border border-line bg-surface px-2 py-3.5 text-center text-xs font-semibold text-ink transition duration-1 ease-m hover:border-ink/15 hover:shadow-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
                  <a.icon size={20} className="text-plum" aria-hidden="true" />
                  {a.label}
                </Link>
              ))}
          </section>

          {/* saved items */}
          <section className="mt-4 overflow-hidden rounded-lg border border-line bg-surface">
            <div className="flex items-center justify-between px-4 py-3">
              <h2 className="flex items-center gap-2 font-display text-base font-bold text-ink">
                <Heart size={16} className="text-plum" aria-hidden="true" /> {S.me.saved}
              </h2>
              {savedItems.length > 0 && (
                <div className="flex items-center gap-3">
                  <Link to="/shop?f=saved" className="text-sm font-semibold text-plum hover:underline">
                    {S.home.seeAll}
                  </Link>
                  <Button size="sm" onClick={() => mkt.addMany(savedItems.filter((it) => it.stock_status !== 'out_of_stock').map((it) => ({ item: it, qty: mkt.defaultQty(it) })), 'saved')}>
                    {S.me.addAllSaved(savedItems.length)}
                  </Button>
                </div>
              )}
            </div>
            {savedItems.length === 0 ? (
              <p className="border-t border-line-2 px-4 py-4 text-sm text-ink-2">{S.me.savedEmpty}</p>
            ) : (
              <ul className="divide-y divide-line-2 border-t border-line-2">
                {savedItems.slice(0, 6).map((it) => (
                  <li key={it.item_code} className="px-4">
                    <MarketCard item={it} variant="list" from="saved" />
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* orders */}
          <section className="mt-4 overflow-hidden rounded-lg border border-line bg-surface">
            <div className="flex items-center justify-between px-4 py-3">
              <h2 className="font-display text-base font-bold text-ink">{S.me.orders}</h2>
              {myOrders.length > 0 && (
                <Link to="/orders" className="text-sm font-semibold text-plum hover:underline">
                  {S.orders.all}
                </Link>
              )}
            </div>
            {myOrders.length === 0 ? (
              <p className="border-t border-line-2 px-4 py-4 text-sm text-ink-2">{S.orders.empty}</p>
            ) : (
              <ul className="divide-y divide-line-2 border-t border-line-2">
                {myOrders.slice(0, 5).map((o) => (
                  <li key={o.token}>
                    <Link to={`/o/${o.token}`} className={row}>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="tnum">{o.order_no}</span>
                          <Chip tone={o.status === 'cancelled' ? 'bad' : o.status === 'delivered' ? 'ok' : 'plum'}>{o.status_label || o.status}</Chip>
                        </span>
                        <span className="mt-0.5 block text-xs font-normal text-ink-2">{[fmtDate(o.created_at), o.total_bhd != null ? bhd(o.total_bhd) : null].filter(Boolean).join(' · ')}</span>
                      </span>
                      <ChevronRight size={18} className="text-ink-3" aria-hidden="true" />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <div>
          {/* saved details */}
          <section className="mt-4 rounded-lg border border-line bg-surface p-4 lg:mt-0">
            <div className="flex items-center justify-between">
              <h2 className="font-display text-base font-bold text-ink">{S.me.details}</h2>
              <button type="button" onClick={() => (editing ? saveDetails() : setEditing(true))} className="-me-2 inline-flex h-10 items-center rounded-sm px-3 text-sm font-semibold text-plum hover:bg-plum-wash">
                {editing ? S.me.done : S.me.edit}
              </button>
            </div>
            {editing ? (
              <div className="mt-3 grid gap-3">
                {(
                  [
                    ['shop', S.checkout.shop],
                    ['name', S.checkout.name],
                    ['phone', S.checkout.phone],
                    ['area', S.checkout.area],
                    ['email', S.checkout.email],
                  ] as [keyof CustomerDraft, string][]
                ).map(([k, label]) => (
                  <div key={k}>
                    <Label htmlFor={`me-${k}`}>{label}</Label>
                    <Input id={`me-${k}`} value={customer[k]} onChange={(e) => setCustomer((c) => ({ ...c, [k]: e.target.value }))} tall={false} />
                  </div>
                ))}
              </div>
            ) : (
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                {(
                  [
                    [S.checkout.shop, customer.shop],
                    [S.checkout.name, customer.name],
                    [S.checkout.phone, customer.phone],
                    [S.checkout.area, customer.area],
                    [S.checkout.email, customer.email],
                  ] as [string, string][]
                )
                  .filter(([, v]) => v)
                  .map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="text-ink-2">{k}</dt>
                      <dd className="truncate font-medium text-ink">{v}</dd>
                    </div>
                  ))}
                {!customer.phone && !customer.name && <dd className="col-span-2 text-sm text-ink-2">{S.me.noDetails}</dd>}
              </dl>
            )}
            {(customer.phone || customer.name) && !editing && (
              <button type="button" onClick={forget} className="-ms-2 mt-2 inline-flex h-10 items-center gap-1.5 rounded-sm px-2 text-xs font-semibold text-bad hover:bg-bad-soft">
                <Trash2 size={13} aria-hidden="true" /> {S.me.forget}
              </button>
            )}
          </section>

          {/* install */}
          {!isStandalone() && (installable || isIos()) && (
            <section className="mt-4 flex items-center gap-3 rounded-lg border border-line bg-surface p-4">
              <span className="grid h-11 w-11 shrink-0 place-items-center rounded-sm bg-plum-soft text-plum">
                <Download size={20} aria-hidden="true" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-display text-sm font-bold text-ink">{S.me.install}</div>
                <div className="text-xs text-ink-2">{S.me.installHint}</div>
              </div>
              <Button size="sm" onClick={installable ? install : () => toast(S.me.installIos, 'info')}>
                {S.me.install.split(' ')[0]}
              </Button>
            </section>
          )}

          {/* language, about, contact */}
          <section className="mt-4 overflow-hidden rounded-lg border border-line bg-surface">
            <div className={cn(row, 'cursor-default hover:bg-transparent')}>
              <Globe size={18} className="text-plum" aria-hidden="true" />
              <span className="flex-1">{S.me.language}</span>
              <span className="inline-flex rounded-sm border border-line p-0.5 text-xs">
                <span className="rounded-xs bg-ink px-2 py-1 text-white">EN</span>
                <span className="px-2 py-1 text-ink-3" title={S.me.soon}>
                  AR
                </span>
              </span>
            </div>
            {rep?.whatsapp_url && (
              <a href={rep.whatsapp_url} target="_blank" rel="noreferrer" className={cn(row, 'border-t border-line-2')}>
                <MessageCircle size={18} className="text-plum" aria-hidden="true" />
                <span className="flex-1">
                  {S.me.ask}
                  <span className="block text-xs font-normal text-ink-2">{S.me.askHint}</span>
                </span>
                <ChevronRight size={18} className="text-ink-3" aria-hidden="true" />
              </a>
            )}
            <Link to="/about" className={cn(row, 'border-t border-line-2')}>
              <Info size={18} className="text-plum" aria-hidden="true" />
              <span className="flex-1">{S.nav.about}</span>
              <ChevronRight size={18} className="text-ink-3" aria-hidden="true" />
            </Link>
            <div className={cn(row, 'border-t border-line-2 cursor-default hover:bg-transparent')}>
              <Phone size={18} className="text-plum" aria-hidden="true" />
              <span className="flex-1">{S.me.contact}</span>
              {rep?.whatsapp_url ? (
                <AnchorButton href={rep.whatsapp_url} target="_blank" rel="noreferrer" variant="wa" size="sm" icon={<MessageCircle size={14} aria-hidden="true" />}>
                  {S.rep.whatsapp}
                </AnchorButton>
              ) : (
                <span className="text-xs font-normal text-ink-2">{data?.company || S.company}</span>
              )}
            </div>
          </section>

          <div className="mt-6 flex flex-wrap items-center justify-between gap-2 text-2xs text-ink-3">
            <span>
              {S.me.version} {typeof __BUILD_ID__ !== 'undefined' ? __BUILD_ID__ : 'dev'}
            </span>
            <button type="button" onClick={clearAll} className="inline-flex h-10 items-center rounded-sm px-2 font-semibold text-ink-2 hover:bg-plum-wash hover:text-ink">
              {S.me.clear}
            </button>
          </div>
        </div>
      </div>
      <div className="mt-6">
        {regulars.length >= 3 ? (
          <Rail id="me-regular" title={S.home.regular} seeAllTo="/quick?load=regular">
            {regulars.map((r) => (
              <MarketCard key={r.item.item_code} item={r.item} variant="compact" from="me" presetQty={r.qty} />
            ))}
          </Rail>
        ) : best.length > 0 ? (
          <Rail id="me-best" title={S.rails.best} seeAllTo="/shop?sort=popular">
            {best.map((it) => (
              <MarketCard key={it.item_code} item={it} variant="compact" from="me" />
            ))}
          </Rail>
        ) : null}
      </div>
    </div>
  )
}
