import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Clapperboard, MapPin } from 'lucide-react'
import { listTrips, thumbUrl, tripReel } from '@/lib/api'
import { formatDateShort } from '@/lib/utils'
import { Button } from '@/components/ui/button'

export function TripCards() {
  const navigate = useNavigate()
  const { data: trips } = useQuery({ queryKey: ['trips'], queryFn: listTrips })
  const reel = useMutation({
    mutationFn: (id: number) => tripReel(id),
    onSuccess: (plan) => navigate('/studio', { state: { plan } }),
  })

  if (!trips || trips.length === 0) return null

  return (
    <section className="mb-6">
      <div className="mb-3 flex items-end justify-between">
        <h2 className="font-display text-lg">Trips</h2>
        <button
          type="button"
          onClick={() => navigate('/map')}
          className="text-xs text-muted-foreground hover:text-primary"
        >
          Open map
        </button>
      </div>
      <div className="flex gap-3 overflow-x-auto pb-1">
        {trips.map((trip) => (
          <article
            key={trip.id}
            className="glass w-64 shrink-0 overflow-hidden rounded-2xl"
          >
            <button
              type="button"
              onClick={() => navigate(`/?trip=${trip.id}`)}
              className="block w-full text-left"
            >
              <div className="relative h-28 bg-surface">
                {trip.cover_asset_id ? (
                  <img
                    src={thumbUrl(trip.cover_asset_id)}
                    alt=""
                    className="media-frame h-full w-full object-cover"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center text-muted-foreground">
                    <MapPin className="h-6 w-6" />
                  </div>
                )}
              </div>
              <div className="p-3">
                <p className="clamp-1 font-medium">{trip.title}</p>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  {trip.asset_count} clips
                  {trip.start_at ? ` · ${formatDateShort(trip.start_at)}` : ''}
                </p>
              </div>
            </button>
            <div className="border-t border-border px-3 py-2">
              <Button
                size="sm"
                variant="outline"
                className="w-full"
                disabled={reel.isPending}
                onClick={() => reel.mutate(trip.id)}
              >
                <Clapperboard className="h-3.5 w-3.5" /> Make reel
              </Button>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
