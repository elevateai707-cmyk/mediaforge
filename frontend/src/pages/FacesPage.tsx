import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Users, Pencil, Loader2, ImageOff } from 'lucide-react'
import { faceAssets, listFaces, nameFace, thumbUrl } from '@/lib/api'
import type { Asset, FaceCluster } from '@/lib/types'
import { PageHeader } from '@/components/PageHeader'
import { AssetCard } from '@/components/AssetCard'
import { AssetLightbox } from '@/components/AssetLightbox'
import { EmptyState } from '@/components/EmptyState'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'

export function FacesPage() {
  const queryClient = useQueryClient()
  const [naming, setNaming] = useState<FaceCluster | null>(null)
  const [nameInput, setNameInput] = useState('')
  const [browsing, setBrowsing] = useState<FaceCluster | null>(null)
  const [openAsset, setOpenAsset] = useState<Asset | null>(null)

  const { data, isLoading, isError, error } = useQuery({ queryKey: ['faces'], queryFn: listFaces })

  const nameMutation = useMutation({
    mutationFn: ({ clusterId, name }: { clusterId: number; name: string }) =>
      nameFace(clusterId, name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['faces'] })
      setNaming(null)
    },
  })

  const openNameDialog = (c: FaceCluster) => {
    setNameInput(c.name ?? '')
    setNaming(c)
  }

  return (
    <div>
      <PageHeader
        title={
          <>
            Face <span className="neon-text">Clusters</span>
          </>
        }
        description="Detected people grouped into clusters. Name a cluster to make it searchable."
        actions={
          data ? (
            <Badge variant="outline" className="px-3 py-1">
              <Users className="h-3.5 w-3.5" /> {data.clusters.length} clusters
            </Badge>
          ) : undefined
        }
      />

      {isError && (
        <div className="glass rounded-xl border-destructive/40 p-6 text-center text-sm text-destructive">
          {error instanceof Error ? error.message : 'Failed to load faces.'}
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
          {Array.from({ length: 12 }).map((_, i) => (
            <Skeleton key={i} className="aspect-square w-full rounded-xl" />
          ))}
        </div>
      ) : !data || data.clusters.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No clusters yet"
          description="Run a scan and let the AI job finish. Then name a person so Library and Search can find them."
          actionLabel="Go scan"
          actionTo="/settings"
        />
      ) : (
        <motion.div layout className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
          {data.clusters.map((c) => (
            <motion.div
              key={c.id}
              layout
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              whileHover={{ y: -4 }}
              transition={{ duration: 0.25 }}
              className="glass group overflow-hidden rounded-xl"
            >
              <button
                onClick={() => setBrowsing(c)}
                className="relative block aspect-square w-full cursor-pointer overflow-hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label={`Browse cluster ${c.name ?? c.id}`}
              >
                <img
                  src={thumbUrl(c.thumb_asset_id)}
                  alt={c.name ?? `Cluster ${c.id}`}
                  loading="lazy"
                  className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                />
                <div className="absolute inset-0 bg-gradient-to-t from-black/75 via-transparent to-transparent" />
                <Badge
                  variant="secondary"
                  className="absolute right-2 top-2 backdrop-blur-sm"
                >
                  {c.count}
                </Badge>
                <div className="absolute inset-x-0 bottom-0 p-2.5 text-left">
                  <p className="truncate text-sm font-semibold text-white">
                    {c.name ?? `Person #${c.id}`}
                  </p>
                  {!c.name && (
                    <p className="text-[10px] text-white/60">click to browse · name it to search</p>
                  )}
                </div>
              </button>
              <div className="flex items-center gap-1.5 border-t border-border p-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="flex-1 text-xs"
                  onClick={() => openNameDialog(c)}
                >
                  <Pencil className="h-3.5 w-3.5" /> {c.name ? 'Rename' : 'Name'}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="flex-1 text-xs"
                  onClick={() => setBrowsing(c)}
                >
                  Browse
                </Button>
              </div>
            </motion.div>
          ))}
        </motion.div>
      )}

      {/* name dialog */}
      <Dialog open={naming !== null} onOpenChange={(o) => !o && setNaming(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {naming?.name ? 'Rename person' : 'Name this person'}
            </DialogTitle>
            <DialogDescription>
              {naming?.name
                ? `Cluster #${naming.id} is currently named “${naming.name}”.`
                : `Cluster #${naming?.id} has ${naming?.count} detected faces. A name makes them searchable by text.`}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="face-name">Full name</Label>
            <Input
              id="face-name"
              value={nameInput}
              onChange={(e) => setNameInput(e.target.value)}
              placeholder="e.g. Kaleb"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && nameInput.trim() && naming) {
                  nameMutation.mutate({ clusterId: naming.id, name: nameInput.trim() })
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="gradient"
              disabled={!nameInput.trim() || nameMutation.isPending}
              onClick={() => naming && nameMutation.mutate({ clusterId: naming.id, name: nameInput.trim() })}
            >
              {nameMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Save name
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* browse cluster assets */}
      <BrowseDialog
        cluster={browsing}
        onOpenChange={(o) => !o && setBrowsing(null)}
        onPickAsset={(a) => {
          setBrowsing(null)
          setOpenAsset(a)
        }}
      />

      <AssetLightbox assetId={openAsset?.id ?? null} onOpenChange={(o) => !o && setOpenAsset(null)} />
    </div>
  )
}

function BrowseDialog({
  cluster,
  onOpenChange,
  onPickAsset,
}: {
  cluster: FaceCluster | null
  onOpenChange: (open: boolean) => void
  onPickAsset: (asset: Asset) => void
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['face-assets', cluster?.id],
    queryFn: () => faceAssets(cluster?.id as number, 50),
    enabled: cluster !== null,
  })

  return (
    <Dialog open={cluster !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            {cluster?.name ?? `Person #${cluster?.id}`} — {cluster?.count}{' '}
            {cluster?.count === 1 ? 'asset' : 'assets'}
          </DialogTitle>
          <DialogDescription>
            Assets that contain this person.
          </DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="aspect-square w-full rounded-xl" />
            ))}
          </div>
        ) : !data || data.items.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-center">
            <ImageOff className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">No assets found for this cluster.</p>
          </div>
        ) : (
          <div className="grid max-h-[55vh] grid-cols-2 gap-3 overflow-y-auto pr-1 sm:grid-cols-3 md:grid-cols-4">
            {data.items.map((asset) => (
              <AssetCard key={asset.id} asset={asset} onClick={onPickAsset} />
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
