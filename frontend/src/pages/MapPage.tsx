import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet'
import { MapPin } from 'lucide-react'
import { listPlaces, listTrips } from '@/lib/api'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { Badge } from '@/components/ui/badge'
import 'leaflet/dist/leaflet.css'

export function MapPage() {
  const { data: places } = useQuery({ queryKey: ['places'], queryFn: listPlaces })
  const { data: trips } = useQuery({ queryKey: ['trips'], queryFn: listTrips })

  const points = useMemo(
    () => (places ?? []).filter((p) => p.lat != null && p.lon != null),
    [places],
  )

  const center: [number, number] = points[0]
    ? [points[0].lat as number, points[0].lon as number]
    : [53.5461, -113.4938]

  return (
    <div>
      <PageHeader
        title={
          <>
            Place <span className="neon-text">Map</span>
          </>
        }
        description="Offline cities from your library on OpenStreetMap — no Mapbox token."
        actions={
          <Badge variant="outline">{points.length} places</Badge>
        }
      />

      {points.length === 0 ? (
        <EmptyState
          icon={MapPin}
          title="No GPS yet"
          description="Scan folders with iPhone videos or geotagged photos. Edmonton stamps as +53.5461-113.4938/."
          actionLabel="Scan folders"
          actionTo="/settings"
        />
      ) : (
        <div className="glass overflow-hidden rounded-2xl">
          <MapContainer
            center={center}
            zoom={4}
            className="z-0 h-[min(70vh,640px)] w-full"
            scrollWheelZoom
          >
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {points.map((p) => (
              <CircleMarker
                key={`${p.city}-${p.lat}-${p.lon}`}
                center={[p.lat as number, p.lon as number]}
                radius={10}
                pathOptions={{ color: '#e8a317', fillColor: '#4ebdb8', fillOpacity: 0.7 }}
              >
                <Popup>
                  <strong>{p.city}</strong>
                  {p.region ? ` · ${p.region}` : ''}
                  <br />
                  {p.count} assets
                </Popup>
              </CircleMarker>
            ))}
            {(trips ?? [])
              .filter((t) => t.lat != null && t.lon != null)
              .map((t) => (
                <CircleMarker
                  key={`trip-${t.id}`}
                  center={[t.lat as number, t.lon as number]}
                  radius={6}
                  pathOptions={{ color: '#4ebdb8', fillOpacity: 0.4 }}
                >
                  <Popup>{t.title}</Popup>
                </CircleMarker>
              ))}
          </MapContainer>
        </div>
      )}
    </div>
  )
}
