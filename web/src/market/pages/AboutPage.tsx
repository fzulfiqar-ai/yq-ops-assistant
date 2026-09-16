import { useEffect, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { Building2, MessageCircle, ShieldCheck, Truck } from 'lucide-react'
import { RepCard } from '../components/RepCard'
import { useMarket } from '../MarketContext'
import { usePageTitle } from '../shell/ShellContext'
import { S } from '../strings'
import { AnchorButton } from '../ui/Button'

/**
 * /about — the trust pages a serious platform has and a WhatsApp catalog never did: who YQ is,
 * how delivery and returns work, what we keep on the phone, and how to reach us. One scrolling
 * page with anchors (#delivery, #privacy, #contact) so the promise bar can deep-link into it.
 */
function Section({ id, icon: Icon, title, children }: { id: string; icon: typeof Truck; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24 rounded-xl border border-line bg-surface p-5">
      <h2 className="flex items-center gap-2 font-display text-lg font-bold text-ink">
        <Icon size={18} className="text-plum" aria-hidden="true" /> {title}
      </h2>
      <div className="mt-2 space-y-2 text-sm leading-relaxed text-ink-2">{children}</div>
    </section>
  )
}

export default function AboutPage() {
  const { rep, data, settings } = useMarket()
  const { hash } = useLocation()
  usePageTitle(S.about.title, true, `${S.about.title} · ${S.brand}`)
  useEffect(() => {
    if (!hash) return
    const el = document.getElementById(hash.slice(1))
    if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [hash])
  const thr = Number(settings.free_delivery_threshold_bhd || 0)

  return (
    <div className="px-gutter lg:px-0">
      <div className="mx-auto max-w-3xl space-y-4 lg:mx-0 lg:mt-4">
        <h1 className="hidden font-display text-2xl font-bold text-ink lg:block">{S.about.title}</h1>
        <Section id="about" icon={Building2} title={S.me.about}>
          <p>{S.me.aboutText}</p>
          <p>{S.about.company}</p>
        </Section>
        <Section id="delivery" icon={Truck} title={S.about.delivery}>
          <p>{thr > 0 ? S.about.deliveryOver(thr.toFixed(3)) : S.about.deliveryFree}</p>
          <p>{S.about.deliveryHow}</p>
          <p>{S.about.returns}</p>
        </Section>
        <Section id="privacy" icon={ShieldCheck} title={S.about.privacy}>
          <p>{S.about.privacyText}</p>
          <p>{S.about.privacyForget}</p>
        </Section>
        <Section id="contact" icon={MessageCircle} title={S.me.contact}>
          {rep ? <RepCard rep={rep} /> : <p>{S.rep.soon}</p>}
          <p>{data?.company || S.company}</p>
          {rep?.whatsapp_url && (
            <AnchorButton href={rep.whatsapp_url} target="_blank" rel="noreferrer" variant="wa" icon={<MessageCircle size={15} aria-hidden="true" />}>
              {S.rep.whatsapp}
            </AnchorButton>
          )}
        </Section>
      </div>
    </div>
  )
}
