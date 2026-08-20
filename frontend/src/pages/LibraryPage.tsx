import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowDownWideNarrow, ArrowUpNarrowWide, Images, Loader2, X } from 'lucide-react'
import { listAssets, listFaces, listPlaces, type AssetQueryParams } from '@/lib/api'
import type { Asset } from '@/lib/types'
import { HELP_OPEN_EVENT } from '@/lib/help-kb'
import { PageHeader } from '@/components/PageHeader'
import { CommandBar } from '@/components/CommandBar'
import { TripCards } from '@/components/TripCards'
import { AssetCard } from '@/components/AssetCard'
import { AssetLightbox } from '@/components/AssetLightbox'
import { EmptyState } from '@/components/EmptyState'
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

function Field({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={className ?? 'flex min-w-0 flex-col gap-1.5'}>
      <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">{label}</span>
      {children}
    </label>
  )
}

export function LibraryPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tripId = searchParams.get('trip') ? Number(searchParams.get('trip')) : undefined
  const cityChip = searchParams.get('city') || 'all'
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
  const { data: places } = useQuery({ queryKey: ['places'], queryFn: listPlaces })

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
      city: cityChip !== 'all' ? cityChip : undefined,
      trip_id: Number.isFinite(tripId) ? tripId : undefined,
    }),
    [kind, sort, order, q, tag, face, dateFrom, dateTo, cityChip, tripId],
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
  const hasFilters = Boolean(
    (kind !== 'all' && kind) ||
      q.trim() ||
      tag.trim() ||
      (face && face !== 'all') ||
      dateFrom ||
      dateTo ||
      cityChip !== 'all' ||
      Number.isFinite(tripId),
  )

  const clearFilters = () => {
    setKind('all')
    setQ('')
    setTag('')
    setFace('all')
    setDateFrom('')
    setDateTo('')
    setSearchParams({})
  }

  return (
    <div>
      <CommandBar />
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

      <TripCards />

      {places && places.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => {
              const next = new URLSearchParams(searchParams)
              next.delete('city')
              setSearchParams(next)
            }}
            className={`rounded-full border px-2.5 py-1 text-[11px] ${cityChip === 'all' ? 'border-primary/40 bg-primary/15 text-primary' : 'border-border text-muted-foreground'}`}
          >
            All cities
          </button>
          {places.slice(0, 12).map((p) => (
            <button
              key={p.city}
              type="button"
              onClick={() => {
                const next = new URLSearchParams(searchParams)
                next.set('city', p.city)
                setSearchParams(next)
              }}
              className={`rounded-full border px-2.5 py-1 text-[11px] ${cityChip === p.city ? 'border-primary/40 bg-primary/15 text-primary' : 'border-border text-muted-foreground'}`}
            >
              {p.city} ({p.count})
            </button>
          ))}
        </div>
      )}

      {/* filters */}
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass mb-6 grid grid-cols-2 gap-x-3 gap-y-3 rounded-2xl p-4 md:grid-cols-4 lg:grid-cols-8"
      >
        <Field label="Search" className="col-span-2 flex min-w-0 flex-col gap-1.5 lg:col-span-2">
          <Input
            placeholder="Caption, transcript, tags…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </Field>
        <Field label="Kind">
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
        </Field>
        <Field label="Sort">
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
        </Field>
        <Field label="Order">
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
        </Field>
        <Field label="Tag">
          <Input placeholder="e.g. beach" value={tag} onChange={(e) => setTag(e.target.value)} />
        </Field>
        <Field label="Face">
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
        </Field>
        <Field label="From">
          <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} aria-label="From date" />
        </Field>
        <Field label="To">
          <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} aria-label="To date" />
        </Field>
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
        <EmptyState
          icon={Images}
          title={hasFilters ? 'Nothing matches' : 'Library is empty'}
          description={
            hasFilters
              ? 'Clear filters or ask Forge how search and faces work.'
              : 'Scan a folder in Settings, wait for the job tray, then come back. Press ? if you want a walkthrough.'
          }
          actionLabel={hasFilters ? 'Ask Forge' : 'Scan folders'}
          actionTo={hasFilters ? undefined : '/settings'}
          onAction={
            hasFilters
              ? () =>
                  window.dispatchEvent(
                    new CustomEvent(HELP_OPEN_EVENT, { detail: { q: 'Why is my library empty?' } }),
                  )
              : undefined
          }
        />
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
