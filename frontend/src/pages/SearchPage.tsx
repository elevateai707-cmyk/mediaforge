import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Search as SearchIcon, Sparkles, Timer } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { searchAssets } from '@/lib/api'
import type { Asset } from '@/lib/types'
import { PageHeader } from '@/components/PageHeader'
import { AssetCard } from '@/components/AssetCard'
import { AssetLightbox } from '@/components/AssetLightbox'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

export function SearchPage() {
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')
  const [openAsset, setOpenAsset] = useState<Asset | null>(null)

  const { data, isFetching, isError, error } = useQuery({
    queryKey: ['search', query],
    queryFn: () => searchAssets(query, 30),
    enabled: query.trim().length > 0,
    staleTime: 15_000,
  })

  // debounce: run search as you type (live results)
  useEffect(() => {
    const q = input.trim()
    if (!q) {
      setQuery('')
      return
    }
    const t = window.setTimeout(() => setQuery(q), 400)
    return () => window.clearTimeout(t)
  }, [input])

  const scoreChart = useMemo(() => {
    if (!data || data.results.length === 0) return []
    const buckets = new Map<number, number>()
    for (const r of data.results) {
      const s = r.aesthetic_score ?? 0
      const b = Math.floor(s / 2) * 2 // 0-2, 2-4, ...
      buckets.set(b, (buckets.get(b) ?? 0) + 1)
    }
    return [...buckets.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([range, count]) => ({ range: `${range}–${range + 2}`, count }))
  }, [data])

  return (
    <div>
      <PageHeader
        title={
          <>
            Semantic <span className="neon-text">Search</span>
          </>
        }
        description="Ask in plain language — MediaForge fuses vector similarity, transcript full-text and face-name search."
      />

      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass mb-6 rounded-xl p-4"
      >
        <div className="flex gap-2">
          <div className="relative flex-1">
            <SearchIcon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              autoFocus
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder='e.g. "beach sunset clips with Kaleb talking"'
              className="pl-9"
            />
          </div>
          <Button variant="gradient" disabled={!input.trim()} onClick={() => setQuery(input.trim())}>
            <Sparkles className="h-4 w-4" /> Search
          </Button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          Try:
          {[
            'beach sunset clips with Kaleb talking',
            'birthday cake candles',
            'vancouver skyline night',
          ].map((s) => (
            <button
              key={s}
              onClick={() => setInput(s)}
              className="rounded-full border border-border px-2.5 py-1 transition-colors hover:border-primary/50 hover:text-primary cursor-pointer"
            >
              {s}
            </button>
          ))}
        </div>
      </motion.div>

      {query && data && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Badge variant="secondary">
            <Timer className="h-3 w-3" /> {data.took_ms} ms
          </Badge>
          <Badge variant="outline">{data.results.length} results</Badge>
        </div>
      )}

      {isError && (
        <div className="glass rounded-xl border-destructive/40 p-6 text-center text-sm text-destructive">
          {error instanceof Error ? error.message : 'Search failed.'}
        </div>
      )}

      {query.trim() && isFetching && !data && (
        <div className="glass flex items-center gap-2 rounded-xl p-6 text-sm text-muted-foreground">
          <Sparkles className="h-4 w-4 animate-pulse text-primary" /> Searching…
        </div>
      )}

      {data && data.results.length === 0 && (
        <div className="glass rounded-xl p-10 text-center text-sm text-muted-foreground">
          No matches for “{query}”. Try different wording or scan more media.
        </div>
      )}

      {data && data.results.length > 0 && (
        <div className="grid gap-6 lg:grid-cols-[1fr_260px]">
          <motion.div layout className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4 sm:gap-4">
            {data.results.map((asset) => (
              <AssetCard key={asset.id} asset={asset} onClick={setOpenAsset} />
            ))}
          </motion.div>

          {/* result quality chart */}
          <div className="glass h-fit rounded-xl p-4">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Aesthetic score distribution
            </p>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={scoreChart}>
                  <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                  <XAxis
                    dataKey="range"
                    tick={{ fill: '#8b91a7', fontSize: 10 }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis tick={{ fill: '#8b91a7', fontSize: 10 }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <RechartsTooltip
                    cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                    contentStyle={{
                      background: '#14161f',
                      border: '1px solid rgba(255,255,255,0.1)',
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                  />
                  <Bar dataKey="count" fill="url(#scoreGrad)" radius={[4, 4, 0, 0]} />
                  <defs>
                    <linearGradient id="scoreGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#a855f7" />
                      <stop offset="100%" stopColor="#ec4899" />
                    </linearGradient>
                  </defs>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground">
              Ranking fuses CLIP vector similarity + transcript FTS + face names.
            </p>
          </div>
        </div>
      )}

      {!query && (
        <div className="glass flex flex-col items-center gap-2 rounded-xl p-14 text-center">
          <SearchIcon className="h-10 w-10 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            Type a natural-language query above — results appear live.
          </p>
        </div>
      )}

      <AssetLightbox assetId={openAsset?.id ?? null} onOpenChange={(o) => !o && setOpenAsset(null)} />
    </div>
  )
}
