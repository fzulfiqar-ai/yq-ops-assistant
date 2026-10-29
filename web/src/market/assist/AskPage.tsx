import { useId } from 'react'
import { Navigate } from 'react-router-dom'
import { useMarket } from '../MarketContext'
import { usePageTitle } from '../shell/ShellContext'
import { AssistChat } from './AssistChat'
import { useAssistEnabled } from './enabled'
import { A } from './strings'

/** /ask: the assistant as a page inside the shell (a link target, the no-popup route). */
export default function AskPage() {
  const enabled = useAssistEnabled()
  const { status } = useMarket()
  const titleId = useId()
  usePageTitle(A.title, true, `${A.title} · YQ`)
  if (!enabled) return status === 'loading' ? null : <Navigate to="/" replace />
  return (
    <div className="container-m py-3 md:py-6">
      <section
        aria-labelledby={titleId}
        className="mx-auto h-[calc(100dvh-var(--m-header-h,72px)-var(--m-bottom-stack,0px)-var(--m-safe-b,0px)-24px)] min-h-[440px] max-w-2xl overflow-hidden rounded-xl bg-surface shadow-1 ring-1 ring-line md:h-[min(720px,calc(100dvh-10rem))]"
      >
        <AssistChat itemCode={null} titleId={titleId} />
      </section>
    </div>
  )
}
