import { useEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { ArrowDownWideNarrow, ArrowUpNarrowWide, Loader2, Search as SearchIcon, X } from 'lucide-react'
import { listAssets, listFaces, type AssetQueryParams } from '@/lib/api'
import type { Asset } from '@/lib/types'
import { PageHeader } from '@/components/PageHeader'
import { AssetCard } from '@/components/AssetCard'
import { AssetLightbox } from '@/components/AssetLightbox'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'

const PAGE_SIZE = 60

export function LibraryPage() {
  const [kind, setKind] = useState<'photo' | 'video' | 'all'>('all')
  const [sort, setSort] = useState<'taken_at' | 'aesthetic' | 'added'>('taken_at')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [q, setQ] = useState('')
  const [tag, setTag] = useState('')
  const [face, setFace] = useState('all')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [openAsset, setOpenAsset] = useState<Asset | null>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)

  const { data: faces } = useQuery({ queryKey: ['faces'], queryFn: listFaces })

  const params: AssetQueryParams = useMemo(
    () => ({
      kind: kind === 'all' ? undefined : kind,
      sort,
      order,
      q: q.trim() || undefined,
      tag: tag.trim() || undefined,
      face: face === 'all' ? undefined : face,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    }),
    [kind, sort, order, q, tag, face, dateFrom, dateTo],
  )

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
    error,
  } = useInfiniteQuery({
    queryKey: ['assets', params],
    queryFn: ({ pageParam }) => listAssets({ ...params, limit: PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((n, p) => n + p.items.length, 0)
      return loaded < lastPage.total ? loaded : undefined
    },
  })

  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          void fetchNextPage()
        }
      },
      { rootMargin: '600px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  const items = data?.pages.flatMap((p) => p.items) ?? []
  const total = data?.pages[0]?.total ?? 0
  const hasFilters = Boolean((kind !== 'all' && kind) || q.trim() || tag.trim() || (face && face !== 'all') || dateFrom || dateTo)

  const clearFilters = () => {
    setKind('all')
    setQ('')
    setTag('')
    setFace('all')
    setDateFrom('')
    setDateTo('')
  }

  return (
    <div>
      <PageHeader
        title={
          <>
            Media <span className="neon-text">Library</span>
          </>
        }
        description="Browse everything indexed by MediaForge — photos, video, captions and faces."
        actions={
          total > 0 ? (
            <Badge variant="outline" className="px-3 py-1">
              {total.toLocaleString()} assets
            </Badge>
          ) : undefined
        }
      />

      {/* filters */}
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass mb-6 grid grid-cols-2 gap-3 rounded-xl p-4 md:grid-cols-4 lg:grid-cols-8"
      >
        <div className="col-span-2 lg:col-span-2">
          <Input
            placeholder="Search caption / transcript / tags…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <Select value={kind} onValueChange={(v) => setKind(v as typeof kind)}>
          <SelectTrigger>
            <SelectValue placeholder="All kinds" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All kinds</SelectItem>
            <SelectItem value="photo">Photos</SelectItem>
            <SelectItem value="video">Videos</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={(v) => setSort(v as typeof sort)}>
          <SelectTrigger>
            <SelectValue placeholder="Sort" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="taken_at">Taken date</SelectItem>
            <SelectItem value="aesthetic">Aesthetic score</SelectItem>
            <SelectItem value="added">Added date</SelectItem>
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          onClick={() => setOrder((o) => (o === 'desc' ? 'asc' : 'desc'))}
          className="h-10 w-full"
        >
          {order === 'desc' ? (
            <ArrowDownWideNarrow className="h-4 w-4" />
          ) : (
            <ArrowUpNarrowWide className="h-4 w-4" />
          )}
          {order === 'desc' ? 'Newest' : 'Oldest'}
        </Button>
        <Input placeholder="Tag (e.g. beach)" value={tag} onChange={(e) => setTag(e.target.value)} />
        <Select value={face} onValueChange={setFace}>
          <SelectTrigger>
            <SelectValue placeholder="Face" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Any face</SelectItem>
            {(faces?.clusters ?? []).map((f) => (
              <SelectItem key={f.id} value={String(f.id)}>
                {f.name ?? `Cluster #${f.id}`} ({f.count})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} aria-label="From date" />
        <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} aria-label="To date" />
      </motion.div>

      {hasFilters && (
        <div className="mb-4 flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={clearFilters}>
            <X className="h-3.5 w-3.5" /> Clear filters
          </Button>
        </div>
      )}

      {isError && (
        <div className="glass rounded-xl border-destructive/40 p-6 text-center text-sm text-destructive">
          {error instanceof Error ? error.message : 'Failed to load assets.'}
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="aspect-square w-full rounded-xl" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="glass flex flex-col items-center gap-3 rounded-xl p-14 text-center">
          <SearchIcon className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {hasFilters
              ? 'No assets match the current filters.'
              : 'Library is empty. Head to Settings → Scan to index your media folders.'}
          </p>
        </div>
      ) : (
        <>
          <motion.div layout className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 sm:gap-4">
            {items.map((asset) => (
              <AssetCard key={asset.id} asset={asset} onClick={setOpenAsset} />
            ))}
          </motion.div>
          <div ref={sentinelRef} className="h-24" />
          {isFetchingNextPage && (
            <div className="flex justify-center py-4">
              <Loader2 className="h-5 w-5 animate-spin text-primary" />
            </div>
          )}
        </>
      )}

      <AssetLightbox assetId={openAsset?.id ?? null} onOpenChange={(o) => !o && setOpenAsset(null)} />
    </div>
  )
}
