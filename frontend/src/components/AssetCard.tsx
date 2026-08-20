import { motion } from 'framer-motion'
import { Play, Clock, Sparkles, MapPin } from 'lucide-react'
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
  const place = asset.city ?? asset.place_name

  return (
    <motion.button
      layout
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.22, ease: [0.2, 0, 0, 1] }}
      whileHover={{ y: -3 }}
      onClick={() => onClick(asset)}
      className={cn(
        'group relative aspect-square w-full cursor-pointer overflow-hidden rounded-2xl bg-surface text-left outline outline-1 outline-white/10 -outline-offset-1 transition-[transform] duration-200 ease-out active:scale-[0.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
    >
      <img
        src={img}
        alt={asset.caption ?? asset.path}
        loading="lazy"
        className="media-frame absolute inset-0 h-full w-full object-cover transition-transform duration-300 ease-out group-hover:scale-105"
      />
      <div className="absolute inset-0 bg-gradient-to-t from-black/75 via-transparent to-black/25 opacity-90 transition-opacity group-hover:opacity-100" />

      {asset.kind === 'video' && (
        <div className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity duration-200 group-hover:opacity-100">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/85 pl-0.5 shadow-[0_0_24px] shadow-primary/50">
            <Play className="h-5 w-5 fill-primary-foreground text-primary-foreground" />
          </div>
        </div>
      )}

      <div className="absolute left-2 top-2 flex gap-1.5">
        <Badge variant={asset.kind === 'video' ? 'secondary' : 'default'} className="backdrop-blur-sm">
          {asset.kind}
        </Badge>
        {score !== null && score !== undefined && (
          <Badge variant="default" className="backdrop-blur-sm tabular">
            <Sparkles className="h-3 w-3" />
            {score.toFixed(1)}
          </Badge>
        )}
      </div>

      <div className="absolute inset-x-0 bottom-0 p-2.5">
        <p className="clamp-1 text-xs font-medium text-white/95 drop-shadow">
          {asset.caption ?? asset.path.split('/').pop()}
        </p>
        <div className="mt-1 flex items-center gap-2 text-[10px] text-white/70">
          {asset.kind === 'video' && asset.duration !== null && (
            <span className="inline-flex items-center gap-0.5 tabular">
              <Clock className="h-3 w-3" />
              {formatDuration(asset.duration)}
            </span>
          )}
          {place && (
            <span className="inline-flex items-center gap-0.5">
              <MapPin className="h-3 w-3" />
              {place}
            </span>
          )}
          {asset.faces.length > 0 && <span className="clamp-1">{asset.faces.join(', ')}</span>}
        </div>
      </div>
    </motion.button>
  )
}
