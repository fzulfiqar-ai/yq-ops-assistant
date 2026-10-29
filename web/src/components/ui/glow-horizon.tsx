import { NightScene } from '@/boot/BootFrame'

/**
 * The night horizon behind the sign-in pages (pages/AcceptInvite.tsx; the login paints the same
 * NightScene itself). Until ship 2 this was four blurred arcs flying in over 2 s while the whole layer
 * animated `filter: blur()` — a full-screen blur re-rendered every frame, the costliest thing the portal
 * did on integrated graphics. Now: the static night of the served first frame (src/boot/frames.ts),
 * drawn once with gradients, and its one glow blooming once by transform and opacity.
 */
export type GlowHorizonVariant = 'top'

export interface GlowHorizonProps {
  className?: string
  /** the horizon sits at the top; the only one the portal draws */
  variant?: GlowHorizonVariant
}

export default function GlowHorizon({ className }: GlowHorizonProps) {
  return (
    <div className={className}>
      <NightScene glow />
    </div>
  )
}
