import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  MapPin,
  Camera,
  Film,
  Hash,
  FileText,
  MessagesSquare,
  Scissors,
  ExternalLink,
} from 'lucide-react'
import { getAsset, getScenes, getTranscript, proxyUrl, fileUrl, thumbUrl } from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { formatBytes, formatDate, formatDuration, formatTimecode } from '@/lib/utils'

interface AssetLightboxProps {
  assetId: number | null
  onOpenChange: (open: boolean) => void
}

function InfoRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className="flex items-start gap-2 text-sm">
      <span className="mt-0.5 text-muted-foreground">{icon}</span>
      <span className="text-muted-foreground">{label}:</span>
      <span className="break-all text-foreground">{value}</span>
    </div>
  )
}

export function AssetLightbox({ assetId, onOpenChange }: AssetLightboxProps) {
  const open = assetId !== null
  const videoRef = useRef<HTMLVideoElement>(null)
  const [seekTarget, setSeekTarget] = useState<number | null>(null)

  const { data: asset, isLoading } = useQuery({
    queryKey: ['asset', assetId],
    queryFn: () => getAsset(assetId as number),
    enabled: open,
  })

  const { data: transcript } = useQuery({
    queryKey: ['transcript', assetId],
    queryFn: () => getTranscript(assetId as number),
    enabled: open && asset?.has_transcript === true,
  })

  const { data: scenes } = useQuery({
    queryKey: ['scenes', assetId],
    queryFn: () => getScenes(assetId as number),
    enabled: open && asset?.kind === 'video',
  })

  useEffect(() => {
    if (seekTarget !== null && videoRef.current) {
      videoRef.current.currentTime = seekTarget
      void videoRef.current.play()
      setSeekTarget(null)
    }
  }, [seekTarget])

  const mediaSrc = useMemo(() => {
    if (!asset) return null
    return asset.kind === 'video' ? proxyUrl(asset.id) : fileUrl(asset.id)
  }, [asset])

  return (
    <Dialog open={open} onOpenChange={(o) => onOpenChange(o)}>
      <DialogContent className="max-w-4xl">
        {isLoading || !asset ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <Skeleton className="aspect-video w-full" />
            <div className="space-y-3">
              <Skeleton className="h-6 w-2/3" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
              <Skeleton className="h-4 w-4/6" />
            </div>
          </div>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle className="pr-8">
                <span className="neon-text">#{asset.id}</span>{' '}
                <span className="text-foreground">{asset.caption ?? asset.path.split('/').pop()}</span>
              </DialogTitle>
              <DialogDescription className="truncate">{asset.path}</DialogDescription>
            </DialogHeader>

            <div className="grid gap-5 md:grid-cols-2">
              {/* media */}
              <div className="overflow-hidden rounded-xl border border-border bg-black/40">
                {asset.kind === 'video' ? (
                  <video
                    ref={videoRef}
                    src={mediaSrc ?? undefined}
                    controls
                    poster={thumbUrl(asset.id)}
                    className="aspect-video w-full object-contain"
                  />
                ) : (
                  <img
                    src={mediaSrc ?? undefined}
                    alt={asset.caption ?? asset.path}
                    className="max-h-[60vh] w-full object-contain"
                  />
                )}
              </div>

              {/* metadata */}
              <div className="space-y-4">
                <div className="space-y-2 rounded-xl bg-white/[0.03] p-4">
                  <InfoRow icon={<Film className="h-4 w-4" />} label="Kind" value={asset.kind} />
                  <InfoRow
                    icon={<Camera className="h-4 w-4" />}
                    label="Camera"
                    value={[asset.camera_make, asset.camera_model].filter(Boolean).join(' ') || null}
                  />
                  <InfoRow icon={<Hash className="h-4 w-4" />} label="Size" value={formatBytes(asset.size)} />
                  <InfoRow
                    icon={<Scissors className="h-4 w-4" />}
                    label="Resolution"
                    value={
                      asset.width && asset.height ? `${asset.width} × ${asset.height}` : null
                    }
                  />
                  <InfoRow
                    icon={<Film className="h-4 w-4" />}
                    label="Duration"
                    value={asset.kind === 'video' ? formatDuration(asset.duration) : null}
                  />
                  <InfoRow
                    icon={<MapPin className="h-4 w-4" />}
                    label="GPS"
                    value={
                      asset.gps_lat !== null && asset.gps_lon !== null
                        ? `${asset.gps_lat.toFixed(4)}, ${asset.gps_lon.toFixed(4)}`
                        : null
                    }
                  />
                  <InfoRow icon={<FileText className="h-4 w-4" />} label="Taken" value={formatDate(asset.taken_at)} />
                  <InfoRow icon={<FileText className="h-4 w-4" />} label="Added" value={formatDate(asset.added_at)} />
                </div>

                {(asset.tags.length > 0 || asset.faces.length > 0) && (
                  <div className="flex flex-wrap gap-1.5">
                    {asset.tags.map((t) => (
                      <Badge key={`t-${t}`} variant="outline">
                        #{t}
                      </Badge>
                    ))}
                    {asset.faces.map((f) => (
                      <Badge key={`f-${f}`} variant="secondary">
                        {f}
                      </Badge>
                    ))}
                  </div>
                )}

                {/* scenes */}
                {asset.kind === 'video' && scenes && scenes.length > 0 && (
                  <div className="rounded-xl bg-white/[0.03] p-4">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Scenes ({scenes.length})
                    </p>
                    <div className="max-h-44 space-y-1.5 overflow-y-auto pr-1">
                      {scenes.map((sc) => (
                        <button
                          key={sc.id}
                          onClick={() => setSeekTarget(sc.start)}
                          className="flex w-full items-center justify-between gap-2 rounded-lg border border-transparent px-2 py-1.5 text-left text-xs transition-colors hover:border-border hover:bg-white/5 cursor-pointer"
                        >
                          <span className="clamp-1 flex-1 text-foreground">{sc.caption}</span>
                          <span className="shrink-0 font-mono text-muted-foreground">
                            {formatTimecode(sc.start)} – {formatTimecode(sc.end)}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* transcript */}
                {transcript && transcript.segments.length > 0 && (
                  <div className="rounded-xl bg-white/[0.03] p-4">
                    <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      <MessagesSquare className="h-3.5 w-3.5" /> Transcript
                    </p>
                    <div className="max-h-44 space-y-1.5 overflow-y-auto pr-1">
                      {transcript.segments.map((seg, i) => (
                        <button
                          key={i}
                          onClick={() => setSeekTarget(seg.start)}
                          className="flex w-full items-start gap-2 rounded-lg px-2 py-1 text-left text-xs transition-colors hover:bg-white/5 cursor-pointer"
                        >
                          <span className="mt-0.5 shrink-0 font-mono text-muted-foreground">
                            {formatTimecode(seg.start)}
                          </span>
                          <span className="text-foreground/90">{seg.text}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                <a
                  href={fileUrl(asset.id)}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline"
                >
                  <ExternalLink className="h-3.5 w-3.5" /> Open original file
                </a>
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
