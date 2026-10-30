import type { ReactNode } from 'react'

/** A page's heading: the one page-title style (index.css .page-title, the Command Centre's band uses it too), its
 *  subtitle and its actions. The violet-fuchsia-indigo rule that stood beside the title is gone: the brand is the
 *  plum, and the title's weight carries it. */
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
      <div className="min-w-0">
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
