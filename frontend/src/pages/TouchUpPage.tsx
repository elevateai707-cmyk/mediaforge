import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { ExternalLink, ImagePlus, Images, Loader2, Wand2 } from 'lucide-react'
import {
  TOUCHUP_PRESETS,
  applyTouchup,
  exportedFileUrl,
  listAssets,
  touchupPreviewUrl,
} from '@/lib/api'
import type { Asset } from '@/lib/types'
import { onJobEvent, useWsStore } from '@/lib/ws'
import { AssetCard } from '@/components/AssetCard'
import { PageHeader } from '@/components/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { cn, toPercent } from '@/lib/utils'

/** Pull a server-side output path out of a job message, e.g. "... wrote /mediaforge/exports/touchup/12_denoise.jpg" */
function extractOutputPath(message: string | null): string | null {
  if (!message) return null
  const m = message.match(/(\/[^\s]+\.(?:jpe?g|png|webp|tiff?))/i)
  return m?.[1] ?? null
}

export function TouchUpPage() {
  const [selected, setSelected] = useState<Asset | null>(null)
  const [preset, setPreset] = useState<string>(TOUCHUP_PRESETS[0].id)
  const [previewTs, setPreviewTs] = useState(0)
  const [jobId, setJobId] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [resultPath, setResultPath] = useState<string | null>(null)
  const [resultMessage, setResultMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const { data, isLoading, isError, error: queryError } = useQuery({
    queryKey: ['assets', { kind: 'photo' }],
    queryFn: () => listAssets({ kind: 'photo', limit: 50, sort: 'added', order: 'desc' }),
  })

  const jobs = useWsStore((s) => s.jobs)
  const liveJob = jobId ? jobs[jobId] : undefined
  const isRunning =
    liveJob !== undefined &&
    (liveJob.status === 'running' || liveJob.status === 'queued' || liveJob.status === 'pending')

  // Bump the cache-buster whenever the asset or preset changes so the
  // composite preview refetches instead of showing a stale frame.
  useEffect(() => {
    setPreviewTs(Date.now())
  }, [selected?.id, preset])

  // Watch the WebSocket for this touch-up job and surface the written file.
  useEffect(() => {
    return onJobEvent((job) => {
      if (job.kind !== 'touchup' || job.job_id !== jobId) return
      if (job.status === 'done') {
        const p = extractOutputPath(job.message)
        setResultPath(p)
        setResultMessage(p ? null : job.message)
      } else if (job.status === 'error') {
        setError(job.message ?? 'Touch-up job failed.')
      } else if (job.status === 'cancelled') {
        setError('Touch-up job cancelled.')
      }
    })
  }, [jobId])

  async function handleApply() {
    if (!selected || applying) return
    setApplying(true)
    setError(null)
    setResultPath(null)
    setResultMessage(null)
    try {
      const res = await applyTouchup(selected.id, preset)
      setJobId(res.job_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start touch-up job.')
    } finally {
      setApplying(false)
    }
  }

  const photos = data?.items ?? []
  const previewUrl =
    selected && previewTs > 0 ? `${touchupPreviewUrl(selected.id, preset)}&_=${previewTs}` : null
  const outputUrl = exportedFileUrl(resultPath)
  const presetMeta = TOUCHUP_PRESETS.find((p) => p.id === preset)

  return (
    <div>
      <PageHeader
        title={
          <>
            Photo <span className="neon-text">Touch-up</span>
          </>
        }
        description="Pick a photo, choose an enhancement preset, preview it, then apply non-destructively."
        actions={
          <Badge variant="outline" className="px-3 py-1">
            <Wand2 className="h-3.5 w-3.5" /> Non-destructive · exports/touchup
          </Badge>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
        {/* ---- asset picker ---- */}
        <div className="min-w-0">
          <h2 className="mb-3 text-sm font-semibold text-muted-foreground">1 · Choose a photo</h2>
          {isLoading ? (
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 xl:grid-cols-5">
              {Array.from({ length: 10 }).map((_, i) => (
                <Skeleton key={i} className="aspect-square w-full rounded-xl" />
              ))}
            </div>
          ) : isError ? (
            <div className="glass rounded-xl border-destructive/40 p-6 text-center text-sm text-destructive">
              {queryError instanceof Error ? queryError.message : 'Failed to load photos.'}
            </div>
          ) : photos.length === 0 ? (
            <div className="glass flex flex-col items-center gap-3 rounded-xl p-14 text-center">
              <Images className="h-8 w-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                No photos indexed yet — add media folders in Settings and run a scan.
              </p>
            </div>
          ) : (
            <div className="glass max-h-[560px] overflow-y-auto rounded-xl p-3">
              <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 xl:grid-cols-5">
                {photos.map((asset) => (
                  <AssetCard
                    key={asset.id}
                    asset={asset}
                    onClick={setSelected}
                    className={cn(
                      selected?.id === asset.id &&
                        'ring-2 ring-primary ring-offset-2 ring-offset-background',
                    )}
                  />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ---- controls ---- */}
        <div className="space-y-4">
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="glass rounded-xl p-4"
          >
            <h2 className="mb-3 text-sm font-semibold text-muted-foreground">2 · Pick a preset</h2>
            <Select value={preset} onValueChange={setPreset}>
              <SelectTrigger aria-label="Touch-up preset">
                <SelectValue placeholder="Preset" />
              </SelectTrigger>
              <SelectContent>
                {TOUCHUP_PRESETS.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="mt-2 text-xs text-muted-foreground">
              {presetMeta?.label} —{' '}
              {preset === 'auto_levels' && 'corrects exposure, contrast and color cast automatically'}
              {preset === 'white_balance' && 're-balances color temperature and tint'}
              {preset === 'denoise' && 'removes sensor noise while keeping detail'}
              {preset === 'sharpen' && 'adds crispness to edges and textures'}
              {preset === 'all' && 'runs the full pipeline: levels → balance → denoise → sharpen'}
            </p>
          </motion.div>

          <div className="glass rounded-xl p-4">
            <h2 className="mb-3 text-sm font-semibold text-muted-foreground">
              3 · Before → after preview
            </h2>
            {previewUrl ? (
              <img
                key={previewUrl}
                src={previewUrl}
                alt={`${selected?.caption ?? `Asset ${selected?.id}`} — ${presetMeta?.label} preview`}
                className="w-full rounded-lg border border-border bg-black/40"
              />
            ) : (
              <div className="flex aspect-[4/3] flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-white/[0.02] text-center">
                <ImagePlus className="h-6 w-6 text-muted-foreground" />
                <p className="max-w-[220px] text-xs text-muted-foreground">
                  Select a photo and a preset to render the composite preview.
                </p>
              </div>
            )}
            {selected && (
              <p className="mt-2 truncate text-xs text-muted-foreground" title={selected.path}>
                {selected.path.split('/').pop()}
              </p>
            )}
          </div>

          <div className="glass rounded-xl p-4">
            <h2 className="mb-3 text-sm font-semibold text-muted-foreground">4 · Apply</h2>
            <Button
              variant="gradient"
              className="w-full"
              disabled={!selected || applying || isRunning}
              onClick={handleApply}
            >
              {applying ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : isRunning ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Wand2 className="h-4 w-4" />
              )}
              {isRunning ? 'Enhancing…' : 'Apply touch-up'}
            </Button>

            {liveJob && (
              <div className="mt-4 space-y-2">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span className="capitalize">{liveJob.status}</span>
                  <span>{Math.round(toPercent(liveJob.progress))}%</span>
                </div>
                <Progress value={toPercent(liveJob.progress)} />
                {liveJob.message && (
                  <p className="truncate text-xs text-muted-foreground" title={liveJob.message}>
                    {liveJob.message}
                  </p>
                )}
              </div>
            )}

            {jobId && !isRunning && liveJob?.status !== 'done' && (
              <p className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Job queued — waiting for updates…
              </p>
            )}

            {jobId && liveJob?.status === 'done' && outputUrl && resultPath && (
              <div className="mt-4 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3">
                <p className="text-xs font-medium text-emerald-400">Touch-up complete</p>
                <a
                  href={outputUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-flex max-w-full items-center gap-1.5 text-sm text-primary hover:underline"
                >
                  <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{resultPath.split('/').pop()}</span>
                </a>
                <p className="mt-1 truncate text-[11px] text-muted-foreground" title={resultPath}>
                  {resultPath}
                </p>
              </div>
            )}
            {jobId && liveJob?.status === 'done' && resultMessage && (
              <p className="mt-4 text-xs text-muted-foreground">{resultMessage}</p>
            )}
            {error && <p className="mt-4 text-xs text-destructive">{error}</p>}
          </div>
        </div>
      </div>
    </div>
  )
}
