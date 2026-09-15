import { cn } from '@/lib/utils'

export function Logo({ className, float = false }: { className?: string; float?: boolean }) {
  return (
    <img
      // 160 px WebP covers the largest use (64 px) at 2.5x; the 400 px PNG was 82 KB on every page
      src="/yq-logo-160.webp"
      alt="YQ Bahrain"
      draggable={false}
      className={cn('select-none object-contain', float && 'animate-float', className)}
    />
  )
}
