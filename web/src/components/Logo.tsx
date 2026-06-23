import { cn } from '@/lib/utils'

export function Logo({ className, float = false }: { className?: string; float?: boolean }) {
  return (
    <img
      src="/yq-logo.png"
      alt="YQ Bahrain"
      draggable={false}
      className={cn('select-none object-contain', float && 'animate-float', className)}
    />
  )
}
