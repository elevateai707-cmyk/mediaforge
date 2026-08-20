import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Loader2 } from 'lucide-react'
import { getDedupe, resolveDedupe, thumbUrl } from '@/lib/api'
import type { DedupeAction, DedupePair } from '@/lib/types'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

function filename(path: string): string {
  return path.split('/').pop() || path
}

function PairRow({
  pair,
  busy,
  onResolve,
}: {
  pair: DedupePair
  busy: boolean
  onResolve: (id: string, action: DedupeAction) => void
}) {
  return (
    <article className="glass rounded-2xl p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <Badge variant={pair.kind === 'exact' ? 'warning' : 'outline'}>
          {pair.kind === 'exact' ? 'Exact hash' : `Near · ${pair.distance}`}
        </Badge>
        <span className="font-mono text-[10px] text-muted-foreground">{pair.id}</span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {[
          { id: pair.asset_a, path: pair.path_a, label: 'Keep A' },
          { id: pair.asset_b, path: pair.path_b, label: 'Keep B' },
        ].map((side) => (
          <div key={side.id} className="min-w-0">
            <img
              src={thumbUrl(side.id)}
              alt=""
              className="media-frame mb-2 h-32 w-full rounded-xl object-cover"
              onError={(e) => {
                e.currentTarget.style.visibility = 'hidden'
              }}
            />
            <p className="clamp-1 text-xs text-muted-foreground" title={side.path}>
              #{side.id} · {filename(side.path)}
            </p>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={busy} onClick={() => onResolve(pair.id, 'keep_a')}>
          Keep A
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => onResolve(pair.id, 'keep_b')}>
          Keep B
        </Button>
        <Button size="sm" variant="destructive" disabled={busy} onClick={() => onResolve(pair.id, 'delete_b')}>
          Delete B
        </Button>
      </div>
    </article>
  )
}

export function DedupePage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['dedupe'],
    queryFn: getDedupe,
  })
  const resolve = useMutation({
    mutationFn: ({ pair_id, action }: { pair_id: string; action: DedupeAction }) =>
      resolveDedupe(pair_id, action),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['dedupe'] })
      void queryClient.invalidateQueries({ queryKey: ['assets'] })
    },
  })

  const exact = data?.exact ?? []
  const near = data?.near ?? []
  const pairs = [...exact, ...near]

  return (
    <div>
      <PageHeader
        title={
          <>
            Duplicate <span className="neon-text">Review</span>
          </>
        }
        description="Exact SHA-256 matches and near-duplicate photos (perceptual hash). Keep both, or delete B."
        actions={<Badge variant="outline">{pairs.length} pairs</Badge>}
      />

      {isError && (
        <div className="glass rounded-xl border-destructive/40 p-6 text-center text-sm text-destructive">
          {error instanceof Error ? error.message : 'Failed to load duplicates.'}
        </div>
      )}

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-64 w-full rounded-2xl" />
          ))}
        </div>
      ) : pairs.length === 0 ? (
        <EmptyState
          icon={Copy}
          title="No duplicates found"
          description="Scan more folders, then come back. Exact pairs share a file hash; near pairs are similar stills."
          actionLabel="Scan folders"
          actionTo="/settings"
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {pairs.map((pair) => (
            <PairRow
              key={pair.id}
              pair={pair}
              busy={resolve.isPending}
              onResolve={(id, action) => resolve.mutate({ pair_id: id, action })}
            />
          ))}
        </div>
      )}

      {resolve.isPending && (
        <p className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Applying…
        </p>
      )}
    </div>
  )
}
