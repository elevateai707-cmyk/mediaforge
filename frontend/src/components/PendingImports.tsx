import { useEffect, useState } from 'react'
import { Check, Loader2, X } from 'lucide-react'
import { API_BASE } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { formatBytes } from '@/lib/utils'

type PendingImport = { id: string; source: string; file_count: number; size: number; types: string[] }

export function PendingImports() {
  const [pending, setPending] = useState<PendingImport[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refresh = async () => {
    try {
      const res = await fetch(API_BASE + '/api/import/pending', { credentials: 'include' })
      if (res.status === 401) window.dispatchEvent(new Event('mf-auth-required'))
      if (!res.ok) throw new Error('Could not check removable media.')
      setPending((await res.json() as { pending: PendingImport[] }).pending)
      setError(null)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not check removable media.') }
  }
  useEffect(() => { void refresh(); const id = window.setInterval(() => void refresh(), 10_000); return () => window.clearInterval(id) }, [])
  const act = async (id: string, action: 'approve' | 'dismiss') => {
    setBusy(id); setError(null)
    try {
      const res = await fetch(API_BASE + `/api/import/pending/${id}/${action}`, { method: 'POST', credentials: 'include' })
      if (res.status === 401) window.dispatchEvent(new Event('mf-auth-required'))
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail || `Could not ${action} import.`)
      setPending((current) => current.filter((item) => item.id !== id))
    } catch (e) { setError(e instanceof Error ? e.message : `Could not ${action} import.`) } finally { setBusy(null) }
  }
  if (!pending.length && !error) return null
  return <section className="glass rounded-xl p-4"><div className="flex items-center justify-between gap-3"><div><h2 className="font-semibold">Removable media</h2><p className="text-xs text-muted-foreground">Nothing is copied until you approve it.</p></div><Button size="sm" variant="ghost" onClick={() => void refresh()}>Refresh</Button></div>
    {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    <div className="mt-3 space-y-2">{pending.map((item) => <div key={item.id} className="flex flex-col gap-2 rounded-lg border border-border p-3 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><p className="truncate text-sm">{item.source}</p><p className="text-xs text-muted-foreground">{item.file_count} media files · {formatBytes(item.size)} · {item.types.join(', ').toUpperCase()}</p></div><div className="flex gap-2"><Button size="sm" variant="outline" disabled={busy === item.id} onClick={() => void act(item.id, 'dismiss')}><X className="h-3.5 w-3.5" /> Dismiss</Button><Button size="sm" disabled={busy === item.id} onClick={() => void act(item.id, 'approve')}>{busy === item.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} Import</Button></div></div>)}</div>
  </section>
}
