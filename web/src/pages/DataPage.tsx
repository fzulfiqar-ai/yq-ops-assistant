import { useRef, useState } from 'react'
import { UploadCloud, FileSpreadsheet, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { apiUpload, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

interface IngestResult {
  filename: string
  ingest_ok: boolean
  load_ok: boolean
  ingest_log?: string
  load_log?: string
}

export default function DataPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<IngestResult | null>(null)
  const [error, setError] = useState('')

  async function upload() {
    if (!file) return
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const form = new FormData()
      form.append('file', file)
      setResult(await apiUpload<IngestResult>('/ingest', form))
      setFile(null)
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.body.slice(0, 200)}` : 'Upload failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Data" subtitle="Upload Focus ERP exports to refresh the warehouse" />

      <Card className="p-6">
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDrag(true)
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDrag(false)
            if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0])
          }}
          onClick={() => inputRef.current?.click()}
          className={cn(
            'flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed p-10 text-center transition',
            drag ? 'border-primary bg-accent' : 'border-border hover:border-primary/50',
          )}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xls,.csv"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <div className="grid h-14 w-14 place-items-center rounded-2xl bg-accent text-accent-foreground">
            {file ? <FileSpreadsheet size={26} /> : <UploadCloud size={26} />}
          </div>
          <div className="mt-4 font-display text-base font-semibold">
            {file ? file.name : 'Drop a Focus export here'}
          </div>
          <div className="mt-1 text-sm text-muted-foreground">
            {file ? `${(file.size / 1024).toFixed(0)} KB · click Upload to ingest` : 'or click to browse — .xlsx / .csv'}
          </div>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <Button onClick={upload} disabled={!file || busy}>
            {busy ? <Loader2 className="animate-spin" size={16} /> : <UploadCloud size={16} />}
            {busy ? 'Ingesting…' : 'Upload & ingest'}
          </Button>
          {file && !busy && (
            <button onClick={() => setFile(null)} className="text-sm text-muted-foreground hover:text-foreground">
              Clear
            </button>
          )}
        </div>

        {error && <div className="mt-4 rounded-xl bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div>}

        {result && (
          <div className="mt-5 space-y-2 rounded-xl border bg-secondary/30 p-4 text-sm">
            <div className="font-semibold">{result.filename}</div>
            <div className="flex items-center gap-2">
              {result.ingest_ok ? <CheckCircle2 className="text-emerald-600" size={16} /> : <XCircle className="text-rose-600" size={16} />}
              Parse &amp; stage {result.ingest_ok ? 'succeeded' : 'failed'}
            </div>
            <div className="flex items-center gap-2">
              {result.load_ok ? <CheckCircle2 className="text-emerald-600" size={16} /> : <XCircle className="text-rose-600" size={16} />}
              Load to Supabase {result.load_ok ? 'succeeded' : 'failed'}
            </div>
            {(result.ingest_log || result.load_log) && (
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-muted-foreground">View logs</summary>
                <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-background p-3 text-[11px] text-muted-foreground">
                  {(result.ingest_log || '') + '\n' + (result.load_log || '')}
                </pre>
              </details>
            )}
          </div>
        )}
      </Card>

      <p className="mt-4 text-xs text-muted-foreground">
        Tip: the AI agents and dashboards read from this data. Refresh it daily for live figures — or automate it (Step 4).
      </p>
    </div>
  )
}
