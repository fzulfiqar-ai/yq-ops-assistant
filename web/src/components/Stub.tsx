import { Sparkles } from 'lucide-react'
import { PageHeader } from './PageHeader'
import { Card, CardContent } from './ui/card'

export function Stub({ title, subtitle, note }: { title: string; subtitle?: string; note?: string }) {
  return (
    <div className="animate-fade-up">
      <PageHeader title={title} subtitle={subtitle} />
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <div className="grid h-12 w-12 place-items-center rounded-2xl bg-accent text-accent-foreground">
            <Sparkles size={22} />
          </div>
          <p className="max-w-md text-sm text-muted-foreground">
            {note || 'This page comes alive in the next build phase — the shell, navigation, theming and auth are ready.'}
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
