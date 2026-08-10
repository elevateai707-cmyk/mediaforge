import { motion } from 'framer-motion'
import { Play, Clock, Sparkles } from 'lucide-react'
import type { Asset } from '@/lib/types'
import { posterUrl, thumbUrl } from '@/lib/api'
import { formatDuration } from '@/lib/utils'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'

interface AssetCardProps {
  asset: Asset
  onClick: (asset: Asset) => void
  className?: string
}

export function AssetCard({ asset, onClick, className }: AssetCardProps) {
  const img = asset.kind === 'video' ? posterUrl(asset.id) : thumbUrl(asset.id)
  const score = asset.aesthetic_score

  return (
    <motion.button
      layout
      initial={{ opacity: 0, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      whileHover={{ y: -4, scale: 1.02 }}
      onClick={() => onClick(asset)}
      className={cn(
        'group relative aspect-square w-full overflow-hidden rounded-xl border border-border bg-surface text-left cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
    >
      <img
        src={img}
        alt={asset.caption ?? asset.path}
        loading="lazy"
        className="absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
      />
      <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-black/20 opacity-80 transition-opacity group-hover:opacity-100" />

      {/* video hover overlay */}
      {asset.kind === 'video' && (
        <div className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity duration-200 group-hover:opacity-100">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/80 backdrop-blur-sm shadow-[0_0_20px] shadow-primary/60">
            <Play className="h-5 w-5 fill-white text-white" />
          </div>
        </div>
      )}

      {/* top row */}
      <div className="absolute left-2 top-2 flex gap-1.5">
        <Badge variant={asset.kind === 'video' ? 'secondary' : 'default'} className="backdrop-blur-sm">
          {asset.kind}
        </Badge>
        {score !== null && score !== undefined && (
          <Badge variant="default" className="backdrop-blur-sm">
            <Sparkles className="h-3 w-3" />
            {score.toFixed(1)}
          </Badge>
        )}
      </div>

      {/* bottom info */}
      <div className="absolute inset-x-0 bottom-0 p-2.5">
        <p className="clamp-1 text-xs font-medium text-white/95 drop-shadow">
          {asset.caption ?? asset.path.split('/').pop()}
        </p>
        <div className="mt-1 flex items-center gap-2 text-[10px] text-white/70">
          {asset.kind === 'video' && asset.duration !== null && (
            <span className="inline-flex items-center gap-0.5">
              <Clock className="h-3 w-3" />
              {formatDuration(asset.duration)}
            </span>
          )}
          {asset.faces.length > 0 && <span>{asset.faces.join(', ')}</span>}
        </div>
      </div>
    </motion.button>
  )
}
