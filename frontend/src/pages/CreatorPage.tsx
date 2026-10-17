import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowUpRight, Clapperboard, Film, Lightbulb, Upload } from 'lucide-react'
import { API_BASE, listAssets, thumbUrl, fileUrl } from '@/lib/api'
import { editorRequest, copyText } from '@/lib/editor'
import { creatorRequest, type Analysis, type Brief, type BrollPlan, type CreatorJob } from '@/lib/creator'
import './creator.css'

const terminal = ['done', 'error', 'cancelled', 'interrupted']
type Mode = 'analyse' | 'brainstorm' | 'broll'
export function CreatorPage() {
  const cache = useQueryClient()
  const [mode, setMode] = useState<Mode>('brainstorm')
  const [selected, setSelected] = useState<number[]>([])
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [focus, setFocus] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [jobId, setJobId] = useState(() => localStorage.getItem('mf-creator-job') || '')
  const [topic, setTopic] = useState('')
  const [audience, setAudience] = useState('')
  const [objective, setObjective] = useState('Teach one useful thing and invite a follow')
  const [camera, setCamera] = useState('phone')
  const [seconds, setSeconds] = useState(30)
  const [days, setDays] = useState(3)
  const [platform, setPlatform] = useState('instagram')
  const [fit, setFit] = useState('contain')
  const [refresh, setRefresh] = useState(false)
  const [allLibrary, setAllLibrary] = useState(false)
  const [brief, setBrief] = useState<Brief | null>(null)
  const [plans, setPlans] = useState<BrollPlan[]>([])
  const [at, setAt] = useState(0)
  const [ratio, setRatio] = useState('original')
  const [still, setStill] = useState('')
  const assets = useQuery({ queryKey: ['creator-assets', query, offset], queryFn: () => listAssets({ q: query, offset, limit: 24, sort: 'added', order: 'desc' }) })
  const config = useQuery({ queryKey: ['creator-config'], queryFn: () => creatorRequest<{ cameras: Record<string, { name: string; settings: string }>; caption_style: string; ai_route: string }>('/cameras') })
  const saved = useQuery({ queryKey: ['creator-briefs'], queryFn: () => creatorRequest<Brief[]>('/briefs') })
  const report = useQuery({ queryKey: ['creator-report', focus], queryFn: () => creatorRequest<Analysis>(`/analysis/${focus}`), enabled: focus !== null })
  const job = useQuery({ queryKey: ['creator-job', jobId], queryFn: () => editorRequest<CreatorJob>(`/api/jobs/${jobId}`), enabled: !!jobId,
    refetchInterval: q => q.state.data && terminal.includes(q.state.data.status) ? false : 1500, retry: 1 })
  const running = !!jobId && !job.isError && (!job.data || !terminal.includes(job.data.status))
  useEffect(() => { if (jobId) localStorage.setItem('mf-creator-job', jobId) }, [jobId])
  useEffect(() => {
    if (!job.data || !terminal.includes(job.data.status)) return
    if (job.data.status === 'done') {
      const result = job.data.meta?.result
      if (result && 'creative' in result) setBrief(result)
      if (result && 'completed' in result) setMessage(`Analysis finished: ${result.completed.length} reports saved; ${result.errors.length} files could not be analysed. Open each report to check coverage.`)
      void cache.invalidateQueries({ queryKey: ['creator-report'] })
      void cache.invalidateQueries({ queryKey: ['creator-briefs'] })
    } else if (job.data.status === 'error' || job.data.status === 'interrupted') setError(job.data.message)
  }, [job.data, cache])
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('')
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : 'The request failed') }
    finally { setBusy(false) }
  }
  function toggle(id: number) {
    setSelected(ids => ids.includes(id) ? ids.filter(x => x !== id) : ids.length < 100 ? [...ids, id] : ids)
    setFocus(id); setAt(0); setStill('')
  }
  async function upload(files: FileList | null) {
    if (!files) return
    await action(async () => {
      for (const file of Array.from(files)) {
        const r = await fetch(`${API_BASE}/api/creator/upload?filename=${encodeURIComponent(file.name)}`, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file })
        const data = await r.json()
        if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Upload failed')
        setSelected(ids => [...new Set([...ids, data.asset_id])].slice(0, 100)); setFocus(data.asset_id)
        setMessage(`Uploaded ${file.name}${data.duplicate ? ' (already in library)' : ''}`)
      }
      await cache.invalidateQueries({ queryKey: ['creator-assets'] })
    })
  }
  async function start(path: string, body: unknown) {
    const result = await creatorRequest<{ job_id: string }>(path, body); setJobId(result.job_id)
  }
  const lock = busy || running
  return <div className="creator">
    <header className="creator-hero">
      <div><p className="creator-eyebrow">MEDIAFORGE / BUILD WITH DB</p><h1>Make the next<br /><em>good story.</em></h1><p>Find the moment. Plan the shoot. Turn your footage into a daily edit.</p></div>
      <div className="creator-brand"><Clapperboard size={40} strokeWidth={1} /><span>YOUR FOOTAGE.<br />YOUR POINT OF VIEW.</span><small>Gold · black · cream</small></div>
    </header>
    <nav className="creator-tabs" aria-label="Creator modes">
      {([{ id: 'analyse', title: 'Analyse clips', icon: Film }, { id: 'brainstorm', title: 'Brainstorm & film', icon: Lightbulb }, { id: 'broll', title: 'Daily B-roll', icon: Clapperboard }] as const).map(t => <button key={t.id} aria-pressed={mode === t.id} onClick={() => setMode(t.id)}><t.icon size={18} />{t.title}</button>)}
    </nav>
    {(error || assets.error || config.error || saved.error || report.error || job.error) && <div role="alert" className="creator-alert">{error || String(assets.error || config.error || saved.error || report.error || job.error)}</div>}
    {message && <p role="status" className="creator-note">{message}</p>}
    {job.data && <div className="creator-job" role="status"><div><strong>{job.data.status}</strong> · {job.data.message}</div><progress value={job.data.progress} max={1} />{running && <button onClick={() => void action(async () => { await creatorRequest(`/jobs/${jobId}/cancel`, {}); await job.refetch() })}>Cancel after current model call</button>}</div>}
    <div className="creator-layout">
      <aside className="creator-panel creator-library">
        <div className="creator-heading"><h2>Your footage</h2><span>{selected.length} selected</span></div>
        <p className="creator-muted">Choose up to 100 clips or photos. You can plan a shoot without selecting any.</p>
        <label className="creator-upload"><Upload size={17} />{busy ? 'Working…' : 'Upload video or photos'}<input type="file" multiple accept="video/*,image/*" disabled={lock} onChange={e => { void upload(e.target.files); e.target.value = '' }} /></label>
        <label>Search library<input type="search" value={query} onChange={e => { setQuery(e.target.value); setOffset(0) }} placeholder="Find a clip or subject" /></label>
        <div className="creator-inline"><button onClick={() => setSelected(ids => [...new Set([...ids, ...(assets.data?.items.map(a => a.id) || [])])].slice(0, 100))}>Select page</button><button onClick={() => setSelected([])}>Clear selection</button></div>
        <div className="creator-grid">
          {assets.data?.items.map(a => <button key={a.id} className={selected.includes(a.id) ? 'selected' : ''} aria-pressed={selected.includes(a.id)} onClick={() => toggle(a.id)} title={a.path.split('/').pop()}><img src={thumbUrl(a.id)} alt="" loading="lazy" /><span>{a.path.split('/').pop()}</span><small>{a.kind} · {a.duration ? `${a.duration.toFixed(1)}s` : `${a.width} × ${a.height}`}</small></button>)}
        </div>
        {assets.isLoading && <p>Loading footage…</p>}
        {assets.data?.total === 0 && <p>No matching footage. Upload a clip to get started.</p>}
        <div className="creator-inline"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 24))}>Previous</button><span>{assets.data?.total || 0} assets</span><button disabled={offset + 24 >= (assets.data?.total || 0)} onClick={() => setOffset(offset + 24)}>Next</button></div>
      </aside>
      <main className="creator-work">
        {mode === 'analyse' && <section className="creator-panel">
          <p className="creator-eyebrow">01 / FIND THE MOMENT</p><h2>See what your clips can do.</h2><p>Scene boundaries, sampled visual observations, speech, and timestamped editing suggestions. Coverage is shown in each report.</p>
          <div className="creator-inline"><label className="creator-check"><input type="checkbox" checked={refresh} onChange={e => setRefresh(e.target.checked)} />Refresh existing reports</label><label className="creator-check"><input type="checkbox" checked={allLibrary} onChange={e => setAllLibrary(e.target.checked)} />Analyse entire library ({assets.data?.total || 'all'} assets)</label></div>
          <p className="creator-muted">{config.data?.ai_route === 'cloud' ? 'Cloud AI is enabled in Settings. Analysis sends sampled frames to your configured provider and may use credits.' : 'Analysis uses local AI. Large batches take time; you can cancel between model calls.'}</p>
          <button className="creator-primary" disabled={lock || (!selected.length && !allLibrary)} onClick={() => void action(() => start('/analyse', { asset_ids: selected, all_library: allLibrary, refresh }))}>Analyse {allLibrary ? 'library' : 'selected footage'} <ArrowUpRight size={16} /></button>
          {focus !== null && <div className="creator-report"><h3>Report · asset {focus}</h3>{report.isLoading ? <p>Loading report…</p> : <><p><strong>{report.data?.status.replaceAll('_', ' ')}</strong> · {report.data?.audio_status?.replaceAll('_', ' ')}</p>{report.data?.coverage && <p className="creator-note">{report.data.coverage.visual_samples}/{report.data.coverage.samples} visual samples · {report.data.coverage.scene_ranges} scenes<br />{report.data.coverage.method}</p>}{report.data?.warnings?.map(w => <p className="creator-alert" key={w}>{w}</p>)}<ul>{report.data?.edit_suggestions?.map(s => <li key={s}>{s}</li>)}</ul>{report.data?.moments?.map((m, i) => <details key={i}><summary>{m.start.toFixed(1)}–{m.end.toFixed(1)}s · {m.description}</summary><p>{m.suggestion}</p><p className="creator-muted">Nearest visual sample: {m.sample_at.toFixed(1)}s. Preview the source to check motion.</p></details>)}<details><summary>Speech transcript</summary>{report.data?.transcript?.map((t, i) => <p key={i}>{t.start.toFixed(1)}s · {t.text}</p>)}</details></>}
            <button onClick={() => { setMode('brainstorm'); setSelected(ids => ids.includes(focus) ? ids : [...ids, focus].slice(0, 100)) }}>Use this clip in a brainstorm</button>
          </div>}
        </section>}
        {mode === 'brainstorm' && <section className="creator-panel">
          <p className="creator-eyebrow">02 / BEFORE YOU PRESS RECORD</p><h2>Start with an idea.</h2><p>Get original hook options, what to say, and a shot list for your camera. Analyse selected clips first to ground the plan in what you already filmed.</p>
          <label>What’s the video about?<textarea rows={3} value={topic} onChange={e => setTopic(e.target.value)} placeholder="Example: show how our receptionist handles a missed call while I’m on the tools" maxLength={4000} /></label>
          <div className="creator-fields"><label>Who is watching?<input value={audience} onChange={e => setAudience(e.target.value)} placeholder="Local trades business owners" maxLength={1000} /></label><label>Camera<select value={camera} onChange={e => setCamera(e.target.value)}><option value="phone">Phone / camera</option><option value="mini3pro">DJI Mini 3 Pro</option><option value="neo">DJI Neo (original)</option></select></label></div>
          <label>What should they do next?<input value={objective} onChange={e => setObjective(e.target.value)} maxLength={1000} /></label>
          <div className="creator-fields"><label>Length in seconds<input type="number" min={10} max={90} value={seconds} onChange={e => setSeconds(Number(e.target.value))} /></label><label>Primary platform<select value={platform} onChange={e => setPlatform(e.target.value)}><option value="instagram">Instagram</option><option value="tiktok">TikTok</option><option value="youtube">YouTube Shorts</option></select></label></div>
          <p className="creator-note">{config.data?.cameras[camera]?.settings}</p><p className="creator-muted">{config.data?.caption_style}</p>
          <p className="creator-muted">{config.data?.ai_route === 'cloud' ? 'Uses your cloud AI setting; brief text and selected analysis are sent to that provider.' : 'Uses local AI, with a clearly labelled filming template if the model is unavailable.'}</p>
          <button className="creator-primary" disabled={lock || topic.trim().length < 3 || seconds < 10 || seconds > 90} onClick={() => void action(() => start('/brainstorm', { topic, audience, objective, camera, platforms: [platform], seconds, asset_ids: selected }))}>Build my filming plan <ArrowUpRight size={16} /></button>
        </section>}
        {mode === 'brainstorm' && brief && <section className="creator-panel creator-brief"><div className="creator-heading"><div><p className="creator-eyebrow">{brief.source} / SAVED PLAN</p><h2>{brief.creative.title}</h2></div><button onClick={() => void action(async () => { await copyText(JSON.stringify(brief, null, 2)); setMessage('Filming plan copied') })}>Copy plan</button></div>{brief.notice && <p className="creator-note">{brief.notice}</p>}<h3>Choose your opening</h3>{brief.creative.hooks.map((h, i) => <article className="creator-hook" key={i}><span>0{i + 1}</span><div><h3>{h.text}</h3><p>{h.why}</p></div></article>)}<h3>Your script</h3><p className="creator-script">{brief.creative.script}</p><h3>Film these shots</h3>{brief.creative.shots.map((s, i) => <article className="creator-shot" key={i}><strong>{i + 1}. {s.shot} <small>· {s.seconds}s in edit</small></strong><p>{s.filming}</p><blockquote>{s.say}</blockquote></article>)}<h3>In the edit</h3><ul>{brief.creative.edit_notes.map((n, i) => <li key={i}>{n}</li>)}</ul><h3>Illustrative B-roll prompts</h3>{brief.creative.broll_prompts.map((p, i) => <p className="creator-note" key={i}>{p}</p>)}<p className="creator-muted">Use these prompts in Edit Studio’s generation tools when needed. Creating a plan does not generate paid media.</p><h3>Post caption</h3><p>{brief.creative.post_caption}</p>{brief.camera.source && <a href={brief.camera.source} target="_blank" rel="noreferrer">Camera reference ↗</a>}</section>}
        {mode === 'brainstorm' && !!saved.data?.length && <section className="creator-panel"><h3>Recent filming plans</h3>{saved.data.map(b => <button className="creator-saved" key={b.id} onClick={() => setBrief(b)}>{b.creative.title}<small>{b.source} · {new Date(b.created_at).toLocaleDateString()}</small></button>)}</section>}
        {mode === 'broll' && <section className="creator-panel"><p className="creator-eyebrow">03 / MAKE MORE FROM WHAT YOU HAVE</p><h2>A few clips. A daily edit.</h2><p>Create portrait timelines from your selected footage. Analysed moments are ranked by topic; unanalysed clips start at the beginning. Review framing and movement in Edit Studio before rendering.</p><label>Series topic<input value={topic} onChange={e => setTopic(e.target.value)} maxLength={1000} placeholder="A day on the tools" /></label><div className="creator-fields"><label>Number of drafts<input type="number" min={1} max={14} value={days} onChange={e => setDays(Number(e.target.value))} /></label><label>Seconds per draft<input type="number" min={5} max={60} value={seconds} onChange={e => setSeconds(Number(e.target.value))} /></label><label>Platform<select value={platform} onChange={e => setPlatform(e.target.value)}><option value="instagram">Instagram</option><option value="tiktok">TikTok</option><option value="youtube">YouTube Shorts</option></select></label><label>Framing<select value={fit} onChange={e => setFit(e.target.value)}><option value="contain">Fit · preserve whole frame</option><option value="cover">Fill · centre crop</option></select></label></div><button className="creator-primary" disabled={lock || !selected.length || days < 1 || days > 14 || seconds < 5 || seconds > 60} onClick={() => void action(async () => { const r = await creatorRequest<{ plans: BrollPlan[]; notice: string }>('/broll', { asset_ids: selected, topic: topic || 'Daily highlights', days, seconds, platform, fit }); setPlans(r.plans); setMessage(r.notice) })}>Create daily drafts <ArrowUpRight size={16} /></button><p className="creator-muted">Short selections make shorter edits. Footage may repeat across days. Drafts stay local until you choose to upload them.</p>{plans.map(p => <article className="creator-plan" key={p.plan_id}><div><strong>{p.title}</strong><p>{p.duration.toFixed(1)} seconds · editable draft</p></div><Link to={`/studio?plan_id=${p.plan_id}`}>Open editor ↗</Link></article>)}</section>}
        {mode === 'broll' && <section className="creator-panel"><h2>Pull a photo from the moment.</h2><p>Select a source, choose its timestamp, and save a high-quality JPEG to your library.</p>{focus !== null ? <><p>Source asset {focus}</p><a href={fileUrl(focus)} target="_blank" rel="noreferrer">Open source preview ↗</a><div className="creator-fields"><label>Timestamp (seconds)<input type="number" min={0} step={0.1} value={at} onChange={e => setAt(Number(e.target.value))} /></label><label>Crop<select value={ratio} onChange={e => setRatio(e.target.value)}>{['original', '9:16', '4:5', '1:1'].map(r => <option key={r}>{r}</option>)}</select></label></div><button disabled={lock} onClick={() => void action(async () => { const r = await creatorRequest<{ url: string; notice: string }>('/still', { asset_id: focus, at, ratio }); setStill(r.url); setMessage(r.notice); await cache.invalidateQueries({ queryKey: ['creator-assets'] }) })}>Save still frame</button>{still && <a href={API_BASE + still} download><img className="creator-still" src={API_BASE + still} alt="Extracted source frame" />Download JPEG ↗</a>}</> : <p className="creator-note">Choose a clip or photo from the library.</p>}</section>}
        <footer className="creator-muted">Original footage and wording. Saved reference clips are for analysis only. Resolve handoff is available through the connected MediaForge Resolve MCP after reviewing the edit.</footer>
      </main>
    </div>
  </div>
}
