import type { ReactNode } from 'react'

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: string
  actions?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="relative pl-4">
        {/* gradient accent rule */}
        <span className="absolute left-0 top-1 h-[calc(100%-0.4rem)] w-1 rounded-full bg-gradient-to-b from-violet-500 via-fuchsia-500 to-indigo-500" />
        <h1 className="font-display text-[1.7rem] font-bold leading-tight tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
