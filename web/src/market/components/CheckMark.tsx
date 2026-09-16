/** The order-placed check: circle scales in, the tick draws itself. Once. CSS only. */
export function CheckMark({ size = 56 }: { size?: number }) {
  return (
    <span className="grid place-items-center rounded-full bg-ok-soft text-ok" style={{ width: size, height: size, animation: 'm-scale-in 300ms var(--m-ease-spring) both' }} aria-hidden="true">
      <svg width={size * 0.5} height={size * 0.5} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 12.5l4.5 4.5L19 7.5" style={{ strokeDasharray: 30, strokeDashoffset: 30, animation: 'm-check-draw 400ms var(--m-ease) 220ms forwards' }} />
      </svg>
    </span>
  )
}
