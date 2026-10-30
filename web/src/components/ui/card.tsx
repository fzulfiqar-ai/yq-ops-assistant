import * as React from 'react'
import { cn } from '@/lib/utils'

/** The one card: the .card-luxe surface (index.css: the card colour, a hairline border, an ink-tinted shadow). It
 *  animates nothing itself; a card that lifts does so by transform where it is used. */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('card-luxe rounded-xl text-card-foreground', className)} {...props} />
}
export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex flex-col gap-1 p-5 pb-2', className)} {...props} />
}
export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('font-display text-lg font-semibold leading-tight', className)} {...props} />
}
export function CardDescription({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('text-sm text-muted-foreground', className)} {...props} />
}
export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('p-5 pt-2', className)} {...props} />
}
