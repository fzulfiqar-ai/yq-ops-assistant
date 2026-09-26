import { forwardRef, type AnchorHTMLAttributes, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Link, type LinkProps } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * The market button. One accent (plum) for the primary action; everything else is quiet.
 * Heights are the tap targets: 40 (dense rows), 44 (cards, default), 48 (sheet footers), 56 (hero).
 * The 40px size also carries an invisible 44px hit area on touch screens (`hit`, market.css).
 */

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'wa' | 'ink'
export type ButtonSize = 'sm' | 'md' | 'lg' | 'xl'

const BASE =
  'inline-flex select-none items-center justify-center gap-2 whitespace-nowrap font-semibold transition duration-1 ease-m active:scale-[.985] disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas'

const VARIANT: Record<ButtonVariant, string> = {
  primary: 'bg-plum text-white hover:bg-plum-deep shadow-1',
  secondary: 'border border-line bg-surface text-ink hover:border-ink/20 hover:bg-plum-wash',
  ghost: 'text-ink-2 hover:bg-plum-wash hover:text-ink',
  danger: 'border border-bad/20 bg-bad-soft text-bad hover:bg-bad/10',
  wa: 'bg-wa text-white hover:brightness-95',
  ink: 'bg-ink text-white hover:bg-ink/90',
}

const SIZE: Record<ButtonSize, string> = {
  sm: 'hit relative h-10 rounded-sm px-3.5 text-sm',
  md: 'h-11 rounded-sm px-4 text-sm',
  lg: 'h-12 rounded-md px-5 text-base',
  xl: 'h-14 rounded-md px-6 text-md',
}

// eslint-disable-next-line react-refresh/only-export-components
export function buttonClass(variant: ButtonVariant = 'primary', size: ButtonSize = 'md', className?: string): string {
  return cn(BASE, VARIANT[variant], SIZE[size], className)
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  icon?: ReactNode
  full?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading, icon, full, className, children, disabled, type = 'button', ...props },
  ref,
) {
  return (
    <button ref={ref} type={type} disabled={disabled || loading} className={cn(buttonClass(variant, size), full && 'w-full', className)} {...props}>
      {loading ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : icon}
      {children}
    </button>
  )
})

/** Same look, renders a router <Link>. */
export function LinkButton({
  variant = 'secondary',
  size = 'md',
  icon,
  full,
  className,
  children,
  ...props
}: LinkProps & { variant?: ButtonVariant; size?: ButtonSize; icon?: ReactNode; full?: boolean }) {
  return (
    <Link className={cn(buttonClass(variant, size), full && 'w-full', className)} {...props}>
      {icon}
      {children}
    </Link>
  )
}

/** Same look, renders a plain <a> (WhatsApp, mailto, external). */
export function AnchorButton({
  variant = 'secondary',
  size = 'md',
  icon,
  full,
  className,
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: ButtonVariant; size?: ButtonSize; icon?: ReactNode; full?: boolean }) {
  return (
    <a className={cn(buttonClass(variant, size), full && 'w-full', className)} {...props}>
      {icon}
      {children}
    </a>
  )
}

/** Square icon-only button (header actions, close, back). */
export function IconButton({
  label,
  size = 'md',
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; size?: 'sm' | 'md' | 'lg' }) {
  const dim = size === 'sm' ? 'h-9 w-9' : size === 'lg' ? 'h-12 w-12' : 'h-11 w-11'
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        'grid shrink-0 place-items-center rounded-sm text-ink-2 transition duration-1 ease-m hover:bg-plum-wash hover:text-ink active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
        dim,
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}
