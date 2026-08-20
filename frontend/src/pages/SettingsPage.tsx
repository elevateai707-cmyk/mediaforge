import { useEffect, useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Cpu,
  FolderOpen,
  FolderPlus,
  HardDrive,
  Loader2,
  RefreshCw,
  ScanSearch,
  WifiOff,
  X,
} from 'lucide-react'
import { API_BASE, cancelScan, getConfig, getHealth, listJobs, putConfig, scanPaths } from '@/lib/api'
import { useWsStore } from '@/lib/ws'
import { PageHeader } from '@/components/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { cn, formatDate, toPercent } from '@/lib/utils'

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning'> = {
  done: 'success',
  running: 'default',
  queued: 'warning',
  pending: 'warning',
  error: 'destructive',
  cancelled: 'outline',
}

const KIND_LABEL: Record<string, string> = {
  scan: 'Scan',
  ai: 'AI analysis',
  render: 'Render',
  touchup: 'Touch-up',
}

export function SettingsPage() {
  const [dirs, setDirs] = useState<string[] | null>(null)
  const [dirInput, setDirInput] = useState('')
  const [dirError, setDirError] = useState<string | null>(null)
  const [scanJobId, setScanJobId] = useState<string | null>(null)
  const [startingScan, setStartingScan] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [scanError, setScanError] = useState<string | null>(null)

  const { data: config } = useQuery({ queryKey: ['config'], queryFn: getConfig })
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: getHealth })
  const {
    data: jobsData,
    isLoading: jobsLoading,
    refetch: refetchJobs,
  } = useQuery({ queryKey: ['jobs'], queryFn: () => listJobs(20) })

  const jobs = useWsStore((s) => s.jobs)
  const wsGpu = useWsStore((s) => s.gpu)
  const scanJob = scanJobId ? jobs[scanJobId] : undefined
  const scanRunning =
    scanJob !== undefined &&
    (scanJob.status === 'running' || scanJob.status === 'queued' || scanJob.status === 'pending')

  // Seed the directory list from backend config once (user edits own the state after that).
  useEffect(() => {
    if (dirs === null && config) setDirs(config.media_dirs)
  }, [config, dirs])

  function addDir(e: FormEvent) {
    e.preventDefault()
    const p = dirInput.trim()
    if (!p.startsWith('/')) {
      setDirError('Enter an absolute path, e.g. /home/bfam/Pictures')
      return
    }
    setDirError(null)
    if (dirs?.includes(p)) {
      setDirInput('')
      return
    }
    setDirs((d) => [...(d ?? []), p])
    setDirInput('')
  }

  function removeDir(p: string) {
    setDirs((d) => (d ?? []).filter((x) => x !== p))
  }

  async function handleScan() {
    if (!dirs || dirs.length === 0) {
      setScanError('Add at least one media directory above.')
      return
    }
    setStartingScan(true)
    setScanError(null)
    try {
      const res = await scanPaths(dirs)
      setScanJobId(res.job_id)
    } catch (e) {
      setScanError(e instanceof Error ? e.message : 'Failed to start scan.')
    } finally {
      setStartingScan(false)
    }
  }

  async function handleCancel() {
    if (!scanJobId || cancelling) return
    setCancelling(true)
    setScanError(null)
    try {
      await cancelScan(scanJobId)
    } catch (e) {
      setScanError(e instanceof Error ? e.message : 'Failed to cancel scan.')
    } finally {
      setCancelling(false)
    }
  }

  const gpuMode = wsGpu?.mode ?? health?.gpu ?? null
  const vram = wsGpu?.vram_mb ?? health?.vram_mb ?? null
  const jobsItems = jobsData?.items ?? []

  return (
    <div>
      <PageHeader
        title={
          <>
            <span className="neon-text">Settings</span> & Scan
          </>
        }
        description="Point MediaForge at your media folders, run the indexer, and watch jobs land."
      />

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ---- media directories + scan ---- */}
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <HardDrive className="h-4 w-4 text-primary" /> Media directories
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <form onSubmit={addDir} className="flex gap-2">
                <Input
                  placeholder="/home/bfam/Pictures"
                  value={dirInput}
                  onChange={(e) => setDirInput(e.target.value)}
                  aria-label="Add media directory path"
                />
                <Button type="submit" variant="secondary">
                  <FolderPlus className="h-4 w-4" /> Add
                </Button>
              </form>
              {dirError && <p className="text-xs text-destructive">{dirError}</p>}

              {dirs === null ? (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading configured directories…
                </div>
              ) : dirs.length === 0 ? (
                <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border py-8 text-center">
                  <FolderOpen className="h-6 w-6 text-muted-foreground" />
                  <p className="text-xs text-muted-foreground">
                    No media directories yet — add one above.
                  </p>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {dirs.map((p) => (
                    <span
                      key={p}
                      className="group inline-flex max-w-full items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 py-1 pl-3 pr-1.5 text-xs text-primary"
                    >
                      <span className="truncate">{p}</span>
                      <button
                        type="button"
                        onClick={() => removeDir(p)}
                        aria-label={`Remove ${p}`}
                        className="rounded-full p-0.5 text-primary/70 hover:bg-primary/20 hover:text-primary cursor-pointer"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              <div className="flex items-center gap-2 border-t border-border pt-4">
                <Button
                  variant="gradient"
                  onClick={handleScan}
                  disabled={!dirs || dirs.length === 0 || startingScan || scanRunning}
                >
                  {startingScan || scanRunning ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <ScanSearch className="h-4 w-4" />
                  )}
                  {scanRunning ? 'Scanning…' : 'Scan folders'}
                </Button>
                {scanRunning && (
                  <Button variant="destructive" onClick={handleCancel} disabled={cancelling}>
                    {cancelling ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    Cancel
                  </Button>
                )}
              </div>

              {scanJob && (
                <div className="space-y-2 rounded-lg border border-border bg-white/[0.02] p-3">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span className="truncate font-mono">{scanJob.job_id.slice(0, 16)}</span>
                    <span>{Math.round(toPercent(scanJob.progress))}%</span>
                  </div>
                  <Progress value={toPercent(scanJob.progress)} />
                  {scanJob.message && (
                    <p className="truncate text-xs text-muted-foreground" title={scanJob.message}>
                      {scanJob.message}
                    </p>
                  )}
                  {scanJob.done !== null && scanJob.done !== undefined && scanJob.total !== null && scanJob.total !== undefined && (
                    <p className="text-[11px] text-muted-foreground">
                      {scanJob.done} / {scanJob.total} files indexed
                    </p>
                  )}
                </div>
              )}
              {scanError && <p className="text-xs text-destructive">{scanError}</p>}
            </CardContent>
          </Card>
        </div>

        {/* ---- system ---- */}
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Cpu className="h-4 w-4 text-primary" /> System
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div
                className={cn(
                  'flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium',
                  gpuMode === 'cuda'
                    ? 'border-primary/40 bg-primary/10 text-primary'
                    : 'border-border bg-white/[0.03] text-muted-foreground',
                )}
              >
                {gpuMode ? <Cpu className="h-4 w-4" /> : <WifiOff className="h-4 w-4" />}
                {gpuMode === 'cuda' ? 'CUDA' : gpuMode === 'cpu' ? 'CPU' : 'Backend offline'}
                {vram !== null && <span className="text-xs opacity-80">{vram} MB VRAM</span>}
                {health?.gpu_name && (
                  <span className="truncate text-xs opacity-80">· {health.gpu_name}</span>
                )}
              </div>

              <div className="flex items-center justify-between gap-3 rounded-xl border border-border px-3 py-2">
                <Label htmlFor="watcher" className="text-sm">
                  Folder watcher
                  <span className="block text-[11px] font-normal text-muted-foreground">
                    Auto-scan when new files land in media dirs
                  </span>
                </Label>
                <Switch
                  id="watcher"
                  checked={config?.watcher_enabled !== false}
                  onCheckedChange={(on) => {
                    void putConfig({ watcher_enabled: on })
                  }}
                />
              </div>

              <dl className="space-y-2 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-muted-foreground">Backend</dt>
                  <dd className="font-mono text-xs">{API_BASE || '/api (Vite proxy)'}</dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-muted-foreground">Version</dt>
                  <dd className="font-mono text-xs">{health?.version ?? '—'}</dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-muted-foreground">Caption model</dt>
                  <dd className="font-mono text-xs">{config?.ollama_model ?? '—'}</dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-muted-foreground">Cloud LLM</dt>
                  <dd className="font-mono text-xs">{config?.use_cloud_llm ? 'on' : 'off'}</dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-muted-foreground">DaVinci Resolve</dt>
                  <dd className="font-mono text-xs">{config?.resolve_available ? 'available' : 'not running'}</dd>
                </div>
              </dl>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* ---- jobs ---- */}
      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span className="flex items-center gap-2">
              <RefreshCw className="h-4 w-4 text-primary" /> Recent jobs
            </span>
            <Button variant="ghost" size="sm" onClick={() => void refetchJobs()}>
              <RefreshCw className="h-3.5 w-3.5" /> Refresh
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {jobsLoading ? (
            <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading jobs…
            </div>
          ) : jobsItems.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No jobs yet — run a scan and activity will show up here.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">Job</th>
                    <th className="py-2 pr-4 font-medium">Kind</th>
                    <th className="py-2 pr-4 font-medium">Status</th>
                    <th className="py-2 pr-4 font-medium">Progress</th>
                    <th className="py-2 pr-4 font-medium">Created</th>
                    <th className="py-2 font-medium">Message</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {jobsItems.map((job) => (
                    <tr key={job.id} className="align-middle">
                      <td className="max-w-[140px] truncate py-2.5 pr-4 font-mono text-xs text-muted-foreground" title={job.id}>
                        {job.id.slice(0, 14)}
                      </td>
                      <td className="py-2.5 pr-4">
                        <Badge variant="outline">{KIND_LABEL[job.kind] ?? job.kind}</Badge>
                      </td>
                      <td className="py-2.5 pr-4">
                        <Badge variant={STATUS_VARIANT[job.status] ?? 'outline'}>{job.status}</Badge>
                      </td>
                      <td className="py-2.5 pr-4">
                        <div className="flex items-center gap-2">
                          <Progress value={toPercent(job.progress)} className="w-20" />
                          <span className="text-xs text-muted-foreground">
                            {Math.round(toPercent(job.progress))}%
                          </span>
                        </div>
                      </td>
                      <td className="py-2.5 pr-4 text-xs text-muted-foreground">
                        {formatDate(job.created_at)}
                      </td>
                      <td className="max-w-[260px] truncate py-2.5 text-xs text-muted-foreground" title={job.message ?? undefined}>
                        {job.message ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
