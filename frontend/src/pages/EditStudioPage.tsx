import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion, Reorder, useDragControls } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import {
  Check,
  Clapperboard,
  Download,
  FileText,
  Film,
  GripVertical,
  List,
  Loader2,
  Music,
  Play,
  Save,
  Scissors,
  Sparkles,
  TriangleAlert,
  Wand2,
} from 'lucide-react'
import {
  ApiError,
  approvePlan,
  exportPlan,
  exportedFileUrl,
  getRenderStatus,
  planEdit,
  startRender,
  thumbUrl,
  updatePlan,
} from '@/lib/api'
import type { Clip, EditPlan, ExportFormat, RenderRatio, RenderStatus } from '@/lib/types'
import { RENDER_RATIOS, ratioDimensions } from '@/lib/types'
import { onJobEvent, useWsStore } from '@/lib/ws'
import { PageHeader } from '@/components/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { cn, clipDuration, formatDuration, formatTimecode, toPercent } from '@/lib/utils'

const TRANSITIONS = ['cut', 'crossfade', 'dip_to_black'] as const

const EXPORT_FORMATS: {
  format: ExportFormat
  label: string
  hint: string
  icon: LucideIcon
}[] = [
  { format: 'resolve', label: 'DaVinci Resolve', hint: 'Import into a running Resolve session', icon: Film },
  { format: 'fcpxml', label: 'FCPXML', hint: 'Final Cut Pro interchange file', icon: FileText },
  { format: 'edl', label: 'EDL', hint: 'CMX3600 edit decision list', icon: List },
  { format: 'capcut', label: 'CapCut', hint: 'draft_content.json for CapCut desktop', icon: Scissors },
]

const EXAMPLE_INTENTS = [
  '60-second highlight reel of the Vancouver trip, upbeat, vertical 9:16 for TikTok',
  'Birthday party recap, warm and sentimental, 16:9 for YouTube',
  'Beach day shorts compilation, punchy cuts, square 1:1 for Instagram',
]

let uidCounter = 0
function nextUid(): string {
  uidCounter += 1
  return `clip-${uidCounter}-${Math.random().toString(36).slice(2, 8)}`
}

function num(v: string): number {
  const n = Number.parseFloat(v)
  return Number.isFinite(n) && n >= 0 ? n : 0
}

interface ClipDraft {
  uid: string
  asset_id: number
  scene_id: number | null
  start: string
  end: string
  caption: string
  transition: string
  score: number
}

function draftFromClip(c: Clip): ClipDraft {
  return {
    uid: nextUid(),
    asset_id: c.asset_id,
    scene_id: c.scene_id,
    start: String(c.start),
    end: String(c.end),
    caption: c.caption,
    transition: c.transition,
    score: c.score,
  }
}

function draftsToClips(drafts: ClipDraft[]): Clip[] {
  return drafts.map((d) => ({
    asset_id: d.asset_id,
    scene_id: d.scene_id,
    start: num(d.start),
    end: num(d.end),
    caption: d.caption,
    transition: d.transition,
    score: d.score,
  }))
}

function clipsEqual(a: Clip, b: Clip): boolean {
  return (
    a.asset_id === b.asset_id &&
    a.scene_id === b.scene_id &&
    Math.abs(a.start - b.start) < 1e-6 &&
    Math.abs(a.end - b.end) < 1e-6 &&
    a.caption === b.caption &&
    a.transition === b.transition &&
    Math.abs(a.score - b.score) < 1e-6
  )
}

function scoreVariant(score: number): 'success' | 'default' | 'outline' {
  if (score >= 8) return 'success'
  if (score >= 6) return 'default'
  return 'outline'
}

function ClipRow({
  draft,
  index,
  locked,
  onChange,
}: {
  draft: ClipDraft
  index: number
  locked: boolean
  onChange: (uid: string, patch: Partial<ClipDraft>) => void
}) {
  const controls = useDragControls()
  const start = num(draft.start)
  const end = num(draft.end)
  const duration = clipDuration({ start, end })

  return (
    <Reorder.Item
      value={draft.uid}
      dragListener={false}
      dragControls={controls}
      whileDrag={{ scale: 1.02, boxShadow: '0 10px 44px rgba(168, 85, 247, 0.35)' }}
      className="glass-strong rounded-xl p-3"
    >
      <div className="flex items-start gap-3">
        <div className="flex shrink-0 flex-col items-center gap-1.5 pt-2">
          <span
            onPointerDown={(e) => controls.start(e)}
            className="cursor-grab touch-none rounded-md p-1 text-muted-foreground transition-colors hover:bg-white/5 hover:text-foreground active:cursor-grabbing"
            title="Drag to reorder"
            aria-label={`Drag clip ${index + 1} to reorder`}
          >
            <GripVertical className="h-5 w-5" />
          </span>
          <span className="text-[10px] font-semibold text-muted-foreground">#{index + 1}</span>
        </div>

        <img
          src={thumbUrl(draft.asset_id)}
          alt=""
          loading="lazy"
          className="h-16 w-24 shrink-0 rounded-lg border border-border bg-white/5 object-cover"
          onError={(e) => {
            e.currentTarget.style.visibility = 'hidden'
          }}
        />

        <div className="grid min-w-0 flex-1 gap-x-3 gap-y-2 sm:grid-cols-2 lg:grid-cols-[110px_110px_minmax(0,1fr)_150px]">
          <div>
            <Label className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              In (s)
            </Label>
            <Input
              type="number"
              min={0}
              step={0.1}
              value={draft.start}
              disabled={locked}
              onChange={(e) => onChange(draft.uid, { start: e.target.value })}
              className="h-8 text-xs"
            />
          </div>
          <div>
            <Label className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Out (s)
            </Label>
            <Input
              type="number"
              min={0}
              step={0.1}
              value={draft.end}
              disabled={locked}
              onChange={(e) => onChange(draft.uid, { end: e.target.value })}
              className="h-8 text-xs"
            />
          </div>
          <div className="sm:col-span-2 lg:col-span-1">
            <Label className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Caption
            </Label>
            <Input
              value={draft.caption}
              disabled={locked}
              onChange={(e) => onChange(draft.uid, { caption: e.target.value })}
              placeholder="On-screen caption"
              className="h-8 text-xs"
            />
          </div>
          <div>
            <Label className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Transition
            </Label>
            <Select
              value={draft.transition}
              disabled={locked}
              onValueChange={(v) => onChange(draft.uid, { transition: v })}
            >
              <SelectTrigger className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRANSITIONS.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t.replace(/_/g, ' ')}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-end justify-between gap-1 self-stretch py-0.5">
          <Badge variant={scoreVariant(draft.score)}>{draft.score.toFixed(1)}</Badge>
          <span className="whitespace-nowrap text-[10px] text-muted-foreground">
            {formatTimecode(start)}–{formatTimecode(end)} · {formatDuration(duration)}
          </span>
        </div>
      </div>
    </Reorder.Item>
  )
}

export function EditStudioPage() {
  // ---- intent → plan ----
  const [intent, setIntent] = useState('')
  const [plan, setPlan] = useState<EditPlan | null>(null)
  const [drafts, setDrafts] = useState<ClipDraft[]>([])
  const [planBusy, setPlanBusy] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)

  // ---- review settings ----
  const [ratio, setRatio] = useState<RenderRatio>('9:16')
  const [width, setWidth] = useState(1080)
  const [height, setHeight] = useState(1920)
  const [captions, setCaptions] = useState(true)
  const [musicPath, setMusicPath] = useState('')
  const [approved, setApproved] = useState(false)

  // ---- save ----
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  // ---- approval + render ----
  const [approving, setApproving] = useState(false)
  const [approveError, setApproveError] = useState<string | null>(null)
  const [renderJobId, setRenderJobId] = useState<string | null>(null)
  const [renderStatus, setRenderStatus] = useState<RenderStatus | null>(null)

  // ---- NLE exports ----
  const [exporting, setExporting] = useState<ExportFormat | null>(null)
  const [exportResults, setExportResults] = useState<Partial<Record<ExportFormat, string>>>({})
  const [exportErrors, setExportErrors] = useState<Partial<Record<ExportFormat, string>>>({})
  const [resolveInfo, setResolveInfo] = useState<{ resolve?: string; project?: string } | null>(null)
  const [exportDialog, setExportDialog] = useState<{ format: ExportFormat; message: string } | null>(null)

  // live render job state from the shared WebSocket store
  const renderLive = useWsStore((s) => (renderJobId ? s.jobs[renderJobId] : undefined))

  const applyPlan = useCallback((p: EditPlan) => {
    setPlan(p)
    setDrafts(p.clips.map((c) => draftFromClip(c)))
    setApproved(p.status === 'approved')
    setPlanError(null)
    setSaveError(null)
    setSavedAt(null)
    if (p.target_ratio === '9:16' || p.target_ratio === '1:1' || p.target_ratio === '16:9') {
      setRatio(p.target_ratio)
      const dims = ratioDimensions(p.target_ratio)
      setWidth(dims.width)
      setHeight(dims.height)
    }
    // fresh plan → clear stale render/export state
    setRenderJobId(null)
    setRenderStatus(null)
    setExportResults({})
    setExportErrors({})
    setResolveInfo(null)
  }, [])

  const generatePlan = async () => {
    const text = intent.trim()
    if (!text || planBusy) return
    setPlanBusy(true)
    setPlanError(null)
    try {
      const p = await planEdit(text)
      applyPlan(p)
    } catch (e) {
      setPlanError(e instanceof Error ? e.message : 'Failed to generate plan.')
    } finally {
      setPlanBusy(false)
    }
  }

  const patchDraft = useCallback((uid: string, patch: Partial<ClipDraft>) => {
    setDrafts((ds) => ds.map((d) => (d.uid === uid ? { ...d, ...patch } : d)))
  }, [])

  const dirty = useMemo(() => {
    if (!plan) return false
    const current = draftsToClips(drafts)
    if (current.length !== plan.clips.length) return true
    return current.some((c, i) => !clipsEqual(c, plan.clips[i] ?? c))
  }, [plan, drafts])

  const runningTotal = useMemo(
    () => drafts.reduce((sum, d) => sum + clipDuration({ start: num(d.start), end: num(d.end) }), 0),
    [drafts],
  )

  const saveEdits = async () => {
    if (!plan || saving || approved) return
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await updatePlan(plan.plan_id, draftsToClips(drafts))
      setPlan(updated)
      setDrafts(updated.clips.map((c) => draftFromClip(c)))
      setSavedAt(Date.now())
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Failed to save edits.')
    } finally {
      setSaving(false)
    }
  }

  // ---- hard approval gate → approve, then render ----
  const approveAndRender = async () => {
    if (!plan || approving) return
    setApproving(true)
    setApproveError(null)
    try {
      await approvePlan(plan.plan_id)
      setApproved(true)
      const job = await startRender({
        plan_id: plan.plan_id,
        ratio,
        music_path: musicPath.trim() || null,
        captions,
        width,
        height,
      })
      setRenderJobId(job.job_id)
      setRenderStatus({ status: 'running', output_path: null, progress: 0 })
    } catch (e) {
      setApproveError(e instanceof Error ? e.message : 'Approval or render start failed.')
    } finally {
      setApproving(false)
    }
  }

  // live WS: when the render job finishes, fetch its output
  useEffect(() => {
    if (!renderJobId) return
    return onJobEvent((job) => {
      if (job.job_id !== renderJobId) return
      if (job.status === 'done') {
        getRenderStatus(renderJobId)
          .then(setRenderStatus)
          .catch(() => {})
      } else if (job.status === 'error') {
        setRenderStatus({ status: 'error', output_path: null, progress: job.progress })
      }
    })
  }, [renderJobId])

  // poll fallback in case the WebSocket is disconnected
  const renderTerminal = renderStatus?.status === 'done' || renderStatus?.status === 'error'
  useEffect(() => {
    if (!renderJobId || renderTerminal) return
    const t = window.setInterval(() => {
      getRenderStatus(renderJobId)
        .then((r) => {
          setRenderStatus(r)
          if (r.status === 'done' || r.status === 'error') window.clearInterval(t)
        })
        .catch(() => {})
    }, 3000)
    return () => window.clearInterval(t)
  }, [renderJobId, renderTerminal])

  const outputUrl = exportedFileUrl(renderStatus?.output_path)
  const renderProgress = renderLive?.progress ?? renderStatus?.progress ?? 0
  const renderRunning = renderJobId !== null && !renderTerminal
  const renderDone = renderStatus?.status === 'done' && outputUrl !== null
  const renderFailed = renderStatus?.status === 'error'

  const handleExport = async (format: ExportFormat) => {
    if (!plan || exporting) return
    setExporting(format)
    setExportErrors((e) => ({ ...e, [format]: undefined }))
    setExportResults((r) => ({ ...r, [format]: undefined }))
    if (format === 'resolve') setResolveInfo(null)
    try {
      const res = await exportPlan(format, plan.plan_id)
      if (format === 'resolve') {
        setResolveInfo({ resolve: res.resolve, project: res.project })
      } else if (res.path) {
        setExportResults((r) => ({ ...r, [format]: exportedFileUrl(res.path) ?? '' }))
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Export failed.'
      if (e instanceof ApiError && e.status === 503) {
        // e.g. DaVinci Resolve not running — surface the backend's helpful message
        setExportDialog({ format, message })
      } else {
        setExportErrors((er) => ({ ...er, [format]: message }))
      }
    } finally {
      setExporting(null)
    }
  }

  const canExport = Boolean(plan) && (approved || renderDone)

  return (
    <div>
      <PageHeader
        title={
          <>
            AI <span className="neon-text">Edit Studio</span>
          </>
        }
        description="Describe the video you want — MediaForge plans the cut, you review and approve it, then it renders and exports to your NLE."
      />

      {/* ---------- intent input ---------- */}
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass mb-6 rounded-xl p-4"
      >
        <Label htmlFor="intent" className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          What should MediaForge make?
        </Label>
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <Textarea
            id="intent"
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            disabled={planBusy}
            placeholder='e.g. "60-second highlight reel of the Vancouver trip, upbeat, vertical 9:16 for TikTok"'
            className="min-h-[92px] flex-1"
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') void generatePlan()
            }}
          />
          <Button
            variant="gradient"
            size="lg"
            disabled={!intent.trim() || planBusy}
            onClick={() => void generatePlan()}
            className="h-auto sm:w-44"
          >
            {planBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
            {planBusy ? 'Planning…' : 'Generate plan'}
          </Button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          Try:
          {EXAMPLE_INTENTS.map((s) => (
            <button
              key={s}
              onClick={() => setIntent(s)}
              className="cursor-pointer rounded-full border border-border px-2.5 py-1 transition-colors hover:border-primary/50 hover:text-primary"
            >
              {s}
            </button>
          ))}
        </div>
        {planError && (
          <div className="mt-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {planError}
          </div>
        )}
      </motion.div>

      {/* ---------- plan review ---------- */}
      {plan && (
        <div className="space-y-4">
          <div className="glass rounded-xl p-5">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={approved ? 'success' : 'warning'}>
                {approved ? 'Approved' : 'Draft'}
              </Badge>
              <Badge variant="outline">plan {plan.plan_id.slice(0, 8)}</Badge>
              <Badge variant="outline">AI target {formatDuration(plan.total_duration)}</Badge>
              <Badge variant="secondary">timeline {formatDuration(runningTotal)}</Badge>
              <Badge variant="outline">{ratio}</Badge>
              {dirty && <Badge variant="warning">Unsaved changes</Badge>}
            </div>
            <p className="mt-3 text-sm leading-relaxed text-foreground/90">{plan.summary}</p>
          </div>

          <div className="grid items-start gap-4 lg:grid-cols-[1fr_300px]">
            {/* timeline */}
            <div className="glass rounded-xl p-4">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                  Timeline · {drafts.length} clips · {formatDuration(runningTotal)}
                </h3>
                <p className="text-xs text-muted-foreground">drag ⋮⋮ to reorder</p>
              </div>
              {drafts.length === 0 ? (
                <p className="py-10 text-center text-sm text-muted-foreground">
                  The plan came back with no clips. Adjust the intent and generate again.
                </p>
              ) : (
                <Reorder.Group
                  axis="y"
                  values={drafts.map((d) => d.uid)}
                  onReorder={(uids) => {
                    const byUid = new Map(drafts.map((d) => [d.uid, d]))
                    const reordered = uids
                      .map((uid) => byUid.get(uid))
                      .filter((d): d is ClipDraft => Boolean(d))
                    setDrafts(reordered)
                  }}
                  className="space-y-3"
                >
                  {drafts.map((d, i) => (
                    <ClipRow key={d.uid} draft={d} index={i} locked={approved} onChange={patchDraft} />
                  ))}
                </Reorder.Group>
              )}
            </div>

            {/* settings */}
            <div className="space-y-4">
              <div className="glass rounded-xl p-4">
                <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                  Output settings
                </h3>

                <Label className="mb-1.5 block text-xs font-medium text-muted-foreground">Ratio</Label>
                <div className="mb-3 flex gap-1 rounded-lg border border-border bg-white/[0.03] p-1">
                  {RENDER_RATIOS.map((r) => (
                    <button
                      key={r}
                      onClick={() => {
                        setRatio(r)
                        const dims = ratioDimensions(r)
                        setWidth(dims.width)
                        setHeight(dims.height)
                      }}
                      className={cn(
                        'flex-1 cursor-pointer rounded-md px-2 py-1.5 text-xs font-medium transition-colors',
                        ratio === r
                          ? 'bg-gradient-to-r from-primary to-secondary text-white'
                          : 'text-muted-foreground hover:text-foreground',
                      )}
                    >
                      {r}
                    </button>
                  ))}
                </div>

                <div className="mb-3 grid grid-cols-2 gap-2">
                  <div>
                    <Label className="mb-1 block text-[10px] uppercase tracking-wider text-muted-foreground">
                      Width
                    </Label>
                    <Input
                      type="number"
                      min={1}
                      value={width}
                      disabled={approved}
                      onChange={(e) => setWidth(Math.max(1, Number(e.target.value) || 1))}
                      className="h-8 text-xs"
                    />
                  </div>
                  <div>
                    <Label className="mb-1 block text-[10px] uppercase tracking-wider text-muted-foreground">
                      Height
                    </Label>
                    <Input
                      type="number"
                      min={1}
                      value={height}
                      disabled={approved}
                      onChange={(e) => setHeight(Math.max(1, Number(e.target.value) || 1))}
                      className="h-8 text-xs"
                    />
                  </div>
                </div>

                <div className="mb-3 flex items-center justify-between gap-3">
                  <Label htmlFor="captions" className="text-xs text-foreground">
                    Burn captions
                    <span className="block text-[10px] font-normal text-muted-foreground">
                      Whisper word timings as burned subtitles
                    </span>
                  </Label>
                  <Switch id="captions" checked={captions} onCheckedChange={setCaptions} disabled={approved} />
                </div>

                <div>
                  <Label className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Music className="h-3.5 w-3.5" /> Music path (optional)
                  </Label>
                  <Input
                    value={musicPath}
                    onChange={(e) => setMusicPath(e.target.value)}
                    disabled={approved}
                    placeholder="/home/bfam/mediaforge/music/beat.mp3"
                    className="h-8 text-xs"
                  />
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    Absolute path on the backend machine — enables beat-synced cuts.
                  </p>
                </div>
              </div>

              <div className="glass rounded-xl p-4">
                <Button
                  variant="outline"
                  className="w-full"
                  disabled={!dirty || saving || approved}
                  onClick={() => void saveEdits()}
                >
                  {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                  {saving ? 'Saving…' : 'Save edits'}
                </Button>
                {approved && (
                  <p className="mt-2 text-center text-[11px] text-muted-foreground">
                    Plan approved — edits are locked.
                  </p>
                )}
                {!approved && dirty && (
                  <p className="mt-2 text-center text-[11px] text-muted-foreground">
                    Unsaved changes — save before approving.
                  </p>
                )}
                {saveError && (
                  <p className="mt-2 text-center text-[11px] text-destructive">{saveError}</p>
                )}
                {savedAt && !dirty && (
                  <p className="mt-2 flex items-center justify-center gap-1 text-[11px] text-emerald-400">
                    <Check className="h-3 w-3" /> Saved {new Date(savedAt).toLocaleTimeString()}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* ---------- hard approval gate ---------- */}
          <div className="neon-border rounded-xl bg-gradient-to-br from-primary/10 via-transparent to-secondary/10 p-5">
            <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-3">
                <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-secondary" />
                <div>
                  <h3 className="font-semibold">Approval gate</h3>
                  <p className="mt-0.5 max-w-xl text-sm text-muted-foreground">
                    Approving locks the timeline and starts the FFmpeg render ({ratio}, {width}×{height}
                    {captions ? ', burned captions' : ''}
                    {musicPath.trim() ? ', beat-synced' : ''}). You can re-render any time after approval.
                  </p>
                </div>
              </div>
              <Button
                variant="gradient"
                size="lg"
                disabled={!plan || approving || (renderRunning && !renderFailed)}
                onClick={() => void approveAndRender()}
                className="shrink-0 shadow-[0_0_40px_-8px] shadow-secondary/60"
              >
                {approving ? (
                  <Loader2 className="h-5 w-5 animate-spin" />
                ) : (
                  <Sparkles className="h-5 w-5" />
                )}
                {approving
                  ? 'Approving & starting render…'
                  : approved
                    ? renderDone
                      ? 'Re-render'
                      : 'Render again'
                    : 'Approve plan & render'}
              </Button>
            </div>
            {approveError && (
              <div className="mt-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {approveError}
              </div>
            )}
          </div>

          {/* ---------- render progress / output ---------- */}
          {(renderJobId || renderStatus || outputUrl) && (
            <div className="glass rounded-xl p-4">
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                <Film className="h-4 w-4" /> Render
              </h3>

              {renderRunning && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span className="flex items-center gap-1.5">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                      {renderLive?.message ?? 'Rendering…'}
                    </span>
                    <span>{Math.round(toPercent(renderProgress))}%</span>
                  </div>
                  <Progress value={toPercent(renderProgress)} />
                </div>
              )}

              {renderFailed && (
                <div className="flex flex-col items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  <span>Render failed. Check the backend logs or re-render.</span>
                  <Button size="sm" variant="destructive" onClick={() => void approveAndRender()}>
                    Retry render
                  </Button>
                </div>
              )}

              {renderDone && outputUrl && (
                <div>
                  <div className="flex items-center gap-2">
                    <Play className="h-4 w-4 text-primary" />
                    <span className="text-xs font-medium text-emerald-400">Render complete</span>
                  </div>
                  <video
                    controls
                    src={outputUrl}
                    className="mt-3 max-h-[420px] w-full rounded-lg border border-border bg-black"
                  />
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <a
                      href={outputUrl}
                      download
                      className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs text-foreground transition-colors hover:bg-white/5 hover:text-primary"
                    >
                      <Download className="h-3.5 w-3.5" /> Download MP4
                    </a>
                    <span className="text-[11px] text-muted-foreground">
                      {outputUrl.split('/').pop()}
                    </span>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ---------- NLE exports ---------- */}
          <div className="glass rounded-xl p-4">
            <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
              <Clapperboard className="h-4 w-4" /> Send to an NLE
            </h3>
            <p className="mb-3 text-xs text-muted-foreground">
              {canExport
                ? 'Timeline exports use the approved plan.'
                : 'Approve the plan (and render once) to unlock exports.'}
            </p>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {EXPORT_FORMATS.map(({ format, label, hint, icon: Icon }) => {
                const url = exportResults[format]
                const err = exportErrors[format]
                const busy = exporting === format
                return (
                  <div key={format} className="rounded-xl border border-border bg-white/[0.02] p-3">
                    <Button
                      variant="outline"
                      className="w-full justify-start"
                      disabled={!canExport || exporting !== null}
                      onClick={() => void handleExport(format)}
                    >
                      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4" />}
                      {busy ? 'Exporting…' : label}
                    </Button>
                    <p className="mt-2 min-h-[28px] text-[10px] leading-snug text-muted-foreground">{hint}</p>
                    {format === 'resolve' && resolveInfo && (
                      <p className="flex items-center gap-1 text-[11px] text-emerald-400">
                        <Check className="h-3 w-3 shrink-0" />
                        Imported into Resolve {resolveInfo.resolve ?? ''}
                        {resolveInfo.project ? ` · ${resolveInfo.project}` : ''}
                      </p>
                    )}
                    {url && (
                      <a
                        href={url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-1 flex items-center gap-1 truncate text-[11px] text-primary hover:underline"
                        title={url}
                      >
                        <Download className="h-3 w-3 shrink-0" />
                        {url.split('/').pop()}
                      </a>
                    )}
                    {err && (
                      <p className="mt-1 text-[11px] leading-snug text-destructive">{err}</p>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {/* ---------- empty state ---------- */}
      {!plan && !planBusy && (
        <div className="glass flex flex-col items-center gap-3 rounded-xl p-14 text-center">
          <Clapperboard className="h-10 w-10 text-muted-foreground/50" />
          <p className="max-w-md text-sm text-muted-foreground">
            Describe the video you want in plain language. MediaForge will pick the best scenes, build
            a draft timeline, and hand it to you for review — then render and export it to your editor.
          </p>
        </div>
      )}

      {/* ---------- export 503 dialog (e.g. Resolve not running) ---------- */}
      <Dialog open={exportDialog !== null} onOpenChange={(o) => !o && setExportDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Export not available</DialogTitle>
            <DialogDescription>
              <span className="mb-2 block text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {EXPORT_FORMATS.find((f) => f.format === exportDialog?.format)?.label ?? 'Export'}
              </span>
              <span className="block whitespace-pre-wrap text-sm text-foreground">
                {exportDialog?.message}
              </span>
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end">
            <Button variant="outline" onClick={() => setExportDialog(null)}>
              Got it
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
