import { MessageCircle, UserRound } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { RepCard as Rep } from '@/lib/shopApi'
import { initials } from '../lib/format'
import { S } from '../strings'
import { AnchorButton } from '../ui/Button'

/**
 * The salesman: trust without takeover. Full card (My YQ, storefront row), a compact line (home),
 * or `mini` — no frame, for a footer column: avatar, name, role and a small WhatsApp button.
 */
export function RepCard({ rep, compact, mini, className }: { rep: Rep; compact?: boolean; mini?: boolean; className?: string }) {
  const small = compact || mini
  return (
    <div className={cn('flex items-center gap-3', mini ? '' : 'rounded-lg border border-line bg-surface', mini ? '' : compact ? 'px-3 py-2' : 'p-3.5', className)}>
      {rep.photo_url ? (
        <img src={rep.photo_url} alt="" width={44} height={44} className={cn('shrink-0 rounded-full object-cover', mini ? 'h-10 w-10 ring-2 ring-surface' : compact ? 'h-9 w-9' : 'h-11 w-11')} />
      ) : (
        <span className={cn('grid shrink-0 place-items-center rounded-full bg-plum-soft font-display text-sm font-bold text-plum-ink', mini ? 'h-10 w-10' : compact ? 'h-9 w-9' : 'h-11 w-11')} aria-hidden="true">
          {initials(rep.name) || <UserRound size={18} />}
        </span>
      )}
      <div className="min-w-0 flex-1">
        {!mini && <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-plum">{S.rep.yours}</div>}
        <div className="truncate font-display text-sm font-bold leading-tight text-ink">{rep.name}</div>
        {(!compact || mini) && <div className="truncate text-xs text-ink-2">{rep.title || S.rep.default}</div>}
      </div>
      {rep.whatsapp_url && (
        <AnchorButton href={rep.whatsapp_url} target="_blank" rel="noreferrer" variant="wa" size={small ? 'sm' : 'md'} icon={<MessageCircle size={15} aria-hidden="true" />} aria-label={S.cart.ask(rep.first_name || rep.name)}>
          {S.rep.whatsapp}
        </AnchorButton>
      )}
    </div>
  )
}
