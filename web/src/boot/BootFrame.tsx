import { useLayoutEffect } from 'react'
import { BOOT_PARTS, type BootKind, type BootPart } from './frames'
import { syncRail, useReleaseBoot } from './handoff'

/**
 * React's copies of the served first frame (src/boot/frames.ts): the same parts, the same classes,
 * styled by the same <style> in the head — so taking over from the served node changes no pixel.
 */
function Parts({ parts, glow }: { parts: readonly BootPart[]; glow?: boolean }) {
  return (
    <>
      {parts.map(([cls, kids], i) => (
        <i key={i} className={cls || undefined} data-in={glow && cls === 'yqb-glow' ? '' : undefined}>
          {kids ? <Parts parts={kids} /> : null}
        </i>
      ))}
    </>
  )
}

/**
 * A whole screen of the frame, for a page that waits: the shell's outline while the user's /me is on
 * the way, the night while a signed-out visit finds out it is one (components/guards.tsx). Taking over
 * from the served node, it lets that node go.
 */
export function BootScreen({ kind }: { kind: BootKind }) {
  useReleaseBoot()
  useLayoutEffect(() => {
    if (kind === 'shell' || kind === 'sales') syncRail(kind)
  }, [kind])
  return (
    <div className={`yqb yqb-${kind}`} data-on="" aria-hidden="true">
      <Parts parts={BOOT_PARTS[kind]} />
    </div>
  )
}

/**
 * The login's night (and the intro's): the served night frame as a background, its one glow blooming
 * once — transform and opacity, 900 ms — when `glow` turns on (the login's arrival).
 */
export function NightScene({ glow = false }: { glow?: boolean }) {
  return (
    <div className="yqb yqb-night" data-on="" aria-hidden="true">
      <Parts parts={BOOT_PARTS.night} glow={glow} />
    </div>
  )
}
