import { useState } from 'react'
import { History, Loader2, Repeat2, Scissors, Shuffle } from 'lucide-react'
import { API_BASE, getPlan } from '@/lib/api'
import type { EditPlan } from '@/lib/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'

type Version = { version: string | number; reason?: string; created_at?: string }

export function PlanTools({ planId, isStory, onPlan }: { planId: string; isStory: boolean; onPlan: (plan: EditPlan) => void }) {
  const [versions, setVersions] = useState<Version[]>([])
  const [beatIndex, setBeatIndex] = useState('1')
  const [assetId, setAssetId] = useState('')
  const [silenceDb, setSilenceDb] = useState('-35')
  const [minSilence, setMinSilence] = useState('0.4')
  const [fillers, setFillers] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const call = async (path: string, init?: RequestInit) => {
    const res = await fetch(API_BASE + path, { credentials: 'include', ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) } })
    if (res.status === 401) window.dispatchEvent(new Event('mf-auth-required'))
    const body = await res.json().catch(() => null)
    if (!res.ok) throw new Error(body?.detail || 'Request failed.')
    return body
  }
  const loadVersions = async () => { setBusy(true); try { const body = await call(`/api/edits/plan/${planId}/versions`); setVersions(body.versions ?? body); setMessage(null) } catch (e) { setMessage(e instanceof Error ? e.message : 'Could not load versions.') } finally { setBusy(false) } }
  const restore = async (version: string | number) => { setBusy(true); try { const body = await call(`/api/edits/plan/${planId}/versions/${version}/restore`, { method: 'POST' }); onPlan(body.plan ?? await getPlan(body.plan_id ?? planId)); setMessage('Version restored.') } catch (e) { setMessage(e instanceof Error ? e.message : 'Could not restore version.') } finally { setBusy(false) } }
  const swap = async () => { setBusy(true); try { const body = await call(`/api/edits/plan/${planId}/swap-broll`, { method: 'POST', body: JSON.stringify({ beat_index: Number(beatIndex), asset_id: Number(assetId) }) }); onPlan(body.plan ?? await getPlan(body.plan_id ?? planId)); setMessage('B-roll swapped.') } catch (e) { setMessage(e instanceof Error ? e.message : 'Could not swap B-roll.') } finally { setBusy(false) } }
  const tighten = async () => { setBusy(true); try { const body = await call('/api/edits/tighten', { method: 'POST', body: JSON.stringify({ plan_id: planId, silence_db: Number(silenceDb), min_silence_s: Number(minSilence), remove_fillers: fillers }) }); onPlan(body.plan ?? await getPlan(body.plan_id)); setMessage('Created a new tightened draft plan.') } catch (e) { setMessage(e instanceof Error ? e.message : 'Could not create a tightened plan.') } finally { setBusy(false) } }
  return <section className="glass rounded-xl p-4"><h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">Plan tools</h3><div className="mt-3 grid gap-4 lg:grid-cols-3">
    <div className="rounded-lg border border-border p-3"><p className="flex items-center gap-1.5 text-sm font-medium"><History className="h-4 w-4" /> Versions</p><Button size="sm" variant="outline" className="mt-2" disabled={busy} onClick={() => void loadVersions()}>Load versions</Button>{versions.length > 0 && <ul className="mt-2 space-y-1 text-xs">{versions.map((version) => <li key={String(version.version)} className="flex items-center justify-between gap-2"><span>{version.reason || `Version ${version.version}`}</span><Button size="sm" variant="ghost" disabled={busy} onClick={() => void restore(version.version)}><Repeat2 className="h-3.5 w-3.5" /> Restore</Button></li>)}</ul>}</div>
    {isStory && <div className="rounded-lg border border-border p-3"><p className="flex items-center gap-1.5 text-sm font-medium"><Shuffle className="h-4 w-4" /> Story B-roll</p><div className="mt-2 grid grid-cols-2 gap-2"><Input aria-label="Beat number (1-based)" type="number" min={1} value={beatIndex} onChange={(e) => setBeatIndex(e.target.value)} placeholder="Beat (1-based)" /><Input aria-label="Replacement asset ID" type="number" min={1} value={assetId} onChange={(e) => setAssetId(e.target.value)} placeholder="Asset ID" /></div><p className="mt-1 text-[10px] text-muted-foreground">Beat number is 1-based. The replacement asset must be available to this story.</p><Button size="sm" variant="outline" className="mt-2" disabled={busy || !assetId} onClick={() => void swap()}>Swap B-roll</Button></div>}
    <div className="rounded-lg border border-border p-3"><p className="flex items-center gap-1.5 text-sm font-medium"><Scissors className="h-4 w-4" /> Silence tighten</p><div className="mt-2 grid grid-cols-2 gap-2"><Input aria-label="Silence threshold dB" type="number" value={silenceDb} onChange={(e) => setSilenceDb(e.target.value)} /><Input aria-label="Minimum silence seconds" type="number" min={0.1} step={0.1} value={minSilence} onChange={(e) => setMinSilence(e.target.value)} /></div><label className="mt-2 flex items-center justify-between text-xs">Remove fillers <Switch checked={fillers} onCheckedChange={setFillers} /></label><Button size="sm" variant="outline" className="mt-2" disabled={busy || isStory} onClick={() => void tighten()}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Scissors className="h-3.5 w-3.5" />} Create tightened plan</Button>{isStory && <p className="mt-2 text-xs text-muted-foreground">Tighten source footage before creating a narrated story to preserve voiceover timing.</p>}</div>
  </div>{message && <p className="mt-3 text-xs text-muted-foreground">{message}</p>}</section>
}
