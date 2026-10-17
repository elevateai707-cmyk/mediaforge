import { useEffect, useState } from 'react'
import { Loader2, Save, Type } from 'lucide-react'
import { API_BASE } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

type CaptionWord = { text: string; start: number; end: number; beat_index?: number }
type QcIssue = { code: string; message: string; cue_index?: number }
type CaptionResponse = { words: CaptionWord[]; qc?: { issues?: QcIssue[]; limitations?: string[] }; detail?: string }

function requireLogin(response: Response) {
  if (response.status === 401) window.dispatchEvent(new Event('mf-auth-required'))
}

export function CaptionWordEditor({ assetId, planId, onSaved }: {
  assetId?: number
  planId?: string
  onSaved?: () => void
}) {
  const path = assetId ? `/api/assets/${assetId}/caption-words` : `/api/edits/plan/${planId}/caption-words`
  const [words, setWords] = useState<CaptionWord[]>([])
  const [issues, setIssues] = useState<QcIssue[]>([])
  const [message, setMessage] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    setUnavailable(false)
    void fetch(API_BASE + path, { credentials: 'include' })
      .then(async (res) => {
        requireLogin(res)
        if (!res.ok) {
          if (res.status === 404 && planId) { if (active) setUnavailable(true); return { words: [] } }
          throw new Error((await res.json().catch(() => null))?.detail || 'Caption words are unavailable.')
        }
        return res.json() as Promise<CaptionResponse>
      })
      .then((body) => { if (active) { setWords(body.words); setIssues(body.qc?.issues ?? []); setMessage(body.qc?.limitations?.join(' ') ?? null) } })
      .catch((error: unknown) => { if (active) setMessage(error instanceof Error ? error.message : 'Caption words are unavailable.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [path, planId])

  const update = (index: number, patch: Partial<CaptionWord>) => {
    setWords((current) => current.map((word, i) => i === index ? { ...word, ...patch } : word))
  }
  const save = async () => {
    setSaving(true); setMessage(null)
    try {
      const res = await fetch(API_BASE + path, {
        method: 'PUT', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ words: words.map(({ text, start, end }) => ({ text, start, end })) }),
      })
      requireLogin(res)
      const body = await res.json().catch(() => null) as CaptionResponse | null
      if (!res.ok) throw new Error(body?.detail || 'Could not save caption words.')
      setWords(body?.words ?? words)
      setIssues(body?.qc?.issues ?? [])
      setMessage(body?.qc?.limitations?.join(' ') ?? 'Saved. Subtitle exports and QC were refreshed.')
      onSaved?.()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save caption words.')
    } finally { setSaving(false) }
  }

  if (loading) return <p className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading word timing…</p>
  if (unavailable) return null
  if (words.length === 0) return <p className="text-xs text-muted-foreground">{message ?? 'No word-level timing is available for this media.'}</p>
  const historicalSegments = Boolean(assetId && words.some((word) => word.text.trim().includes(' ')))
  return <section className="rounded-xl border border-border bg-white/[0.02] p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div><p className="flex items-center gap-1.5 text-sm font-medium"><Type className="h-4 w-4 text-primary" /> Caption words</p><p className="text-[11px] text-muted-foreground">Edit text or timestamps. Times must stay ordered and within the media or story beat.</p>{historicalSegments && <p className="mt-1 text-[11px] text-muted-foreground">Some entries may use historical segment timing rather than word alignment; those boundaries are estimates.</p>}</div>
      <Button size="sm" variant="outline" onClick={() => void save()} disabled={saving}>{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save words</Button>
    </div>
    <div className="mt-3 max-h-64 space-y-2 overflow-y-auto pr-1">
      {words.map((word, index) => <div key={`${word.beat_index ?? 'asset'}-${index}`} className="grid grid-cols-[minmax(0,1fr)_72px_72px] gap-2">
        <Input aria-label={`Caption word ${index + 1}`} value={word.text} onChange={(e) => update(index, { text: e.target.value })} className="h-8 text-xs" />
        <Input aria-label={`Start time ${index + 1}`} type="number" min={0} step={0.01} value={word.start} onChange={(e) => update(index, { start: Number(e.target.value) })} className="h-8 text-xs" />
        <Input aria-label={`End time ${index + 1}`} type="number" min={0} step={0.01} value={word.end} onChange={(e) => update(index, { end: Number(e.target.value) })} className="h-8 text-xs" />
      </div>)}
    </div>
    {issues.length > 0 && <ul className="mt-3 space-y-1 rounded-lg border border-amber-400/25 bg-amber-400/5 p-2 text-xs text-amber-200">{issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul>}
    {message && <p className="mt-2 text-xs text-muted-foreground">{message}</p>}
  </section>
}
