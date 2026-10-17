import type {
  AppConfig,
  Asset,
  AssetCaptionsResponse,
  CaptionFormat,
  CaptionStyle,
  AssetListResponse,
  Clip,
  EditPlan,
  ExportFormat,
  ExportResponse,
  FacesResponse,
  Health,
  Job,
  JobListResponse,
  DedupeAction,
  DedupeOut,
  Place,
  RenderRatio,
  RenderStatus,
  SceneInfo,
  ScenesResponse,
  SearchResponse,
  StartJobResponse,
  StoryJobResponse,
  TranscriptResponse,
  Trip,
} from './types'

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(API_BASE + path, {
      credentials: 'include',
      ...init,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init?.headers ?? {}),
      },
    })
  } catch {
    throw new ApiError(0, `Cannot reach backend at ${API_BASE || '/api'} — is it running on :8420?`)
  }
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new Event('mf-auth-required'))
    let message = `${res.status} ${res.statusText}`
    let detail: unknown
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (body && body.detail !== undefined) {
        detail = body.detail
        message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      }
    } catch {
      // non-JSON error body
    }
    throw new ApiError(res.status, message, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== '') qs.set(k, String(v))
  }
  const s = qs.toString()
  return s ? `?${s}` : ''
}

// ---------- System ----------

export const getHealth = () => request<Health>('/api/health')
export const getConfig = () => request<AppConfig>('/api/config')
export const putConfig = (body: Partial<AppConfig>) =>
  request<AppConfig>('/api/config', { method: 'PUT', body: JSON.stringify(body) })

// ---------- Scan & ingest ----------

export const scanPaths = (paths: string[]) =>
  request<StartJobResponse>('/api/scan', { method: 'POST', body: JSON.stringify({ paths }) })
export const rescanPaths = (paths: string[]) =>
  request<StartJobResponse>('/api/rescan', { method: 'POST', body: JSON.stringify({ paths }) })
export const cancelScan = (job_id: string) =>
  request<{ ok: boolean }>('/api/scan/cancel', { method: 'POST', body: JSON.stringify({ job_id }) })

// ---------- Import / export media ----------

export interface UploadResult {
  ok: boolean
  path: string
  name: string
  size: number
  folder: string
}

export interface ImportFolderResponse extends StartJobResponse {
  src: string
  dest: string
  files: number
  bytes: number
}

export interface MediaCopyResponse extends StartJobResponse {
  dest: string
  files: number
  bytes: number
}

/** Stream one file as a raw request body (no multipart buffering on the server). */
export function uploadFile(
  file: File,
  onProgress?: (loaded: number, total: number) => void,
): { promise: Promise<UploadResult>; abort: () => void } {
  const xhr = new XMLHttpRequest()
  const promise = new Promise<UploadResult>((resolve, reject) => {
    xhr.open('POST', `${API_BASE}/api/import/upload${buildQuery({ filename: file.name })}`)
    xhr.withCredentials = true
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded, e.total)
    }
    xhr.onerror = () => reject(new ApiError(0, 'Upload failed — cannot reach backend'))
    xhr.onabort = () => reject(new ApiError(0, 'Upload cancelled'))
    xhr.onload = () => {
      if (xhr.status === 401) window.dispatchEvent(new Event('mf-auth-required'))
      let body: { detail?: unknown } & Partial<UploadResult> = {}
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        // non-JSON body
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as UploadResult)
      const msg = typeof body.detail === 'string' ? body.detail : `${xhr.status} ${xhr.statusText}`
      reject(new ApiError(xhr.status, msg, body.detail))
    }
    xhr.send(file)
  })
  return { promise, abort: () => xhr.abort() }
}

export const importFolder = (path: string) =>
  request<ImportFolderResponse>('/api/import/folder', {
    method: 'POST',
    body: JSON.stringify({ path }),
  })

export const exportMediaCopy = (asset_ids: number[], dest: string | null, include_sidecars: boolean) =>
  request<MediaCopyResponse>('/api/export/media/copy', {
    method: 'POST',
    body: JSON.stringify({ asset_ids, dest: dest || null, include_sidecars }),
  })

export const downloadUrl = (id: number) => `${API_BASE}/api/assets/${id}/download`
export const zipExportUrl = (ids: number[], sidecars = true) =>
  `${API_BASE}/api/export/media/zip${buildQuery({ ids: ids.join(','), sidecars })}`

/** Trigger a browser download for a URL that answers with Content-Disposition: attachment. */
export function triggerDownload(url: string) {
  const a = document.createElement('a')
  a.href = url
  a.rel = 'noreferrer'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// ---------- Assets ----------

export interface AssetQueryParams {
  limit?: number
  offset?: number
  kind?: 'photo' | 'video'
  sort?: 'taken_at' | 'aesthetic' | 'added'
  order?: 'asc' | 'desc'
  tag?: string
  face?: string
  q?: string
  date_from?: string
  date_to?: string
  city?: string
  trip_id?: number
}

export const listAssets = (params: AssetQueryParams = {}) =>
  request<AssetListResponse>(`/api/assets${buildQuery({ limit: 50, ...params })}`)

export const getAsset = (id: number) => request<Asset>(`/api/assets/${id}`)
export const getTranscript = (id: number) =>
  request<TranscriptResponse>(`/api/assets/${id}/transcript`)
export const getAssetCaptions = (id: number, style: CaptionStyle) =>
  request<AssetCaptionsResponse>(`/api/assets/${id}/captions${buildQuery({ style })}`)
export const captionDownloadUrl = (id: number, format: CaptionFormat, style: CaptionStyle) =>
  `${API_BASE}/api/assets/${id}/captions/${format}${buildQuery({ style })}`
export const getScenes = (id: number) =>
  request<ScenesResponse>(`/api/assets/${id}/scenes`).then((r) => r.scenes as SceneInfo[])

// media URLs (proxied through Vite in dev; absolute when VITE_API_BASE set)
export const thumbUrl = (id: number) => `${API_BASE}/api/assets/${id}/thumb`
export const posterUrl = (id: number) => `${API_BASE}/api/assets/${id}/poster`
export const fileUrl = (id: number) => `${API_BASE}/api/assets/${id}/file`
export const proxyUrl = (id: number) => `${API_BASE}/api/assets/${id}/proxy`

// ---------- Search ----------

export const searchAssets = (q: string, limit = 20) =>
  request<SearchResponse>(`/api/search${buildQuery({ q, limit })}`)

// ---------- Faces ----------

export const listFaces = () => request<FacesResponse>('/api/faces')
export const nameFace = (clusterId: number, name: string) =>
  request<{ ok: boolean }>(`/api/faces/${clusterId}/name`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
export const faceAssets = (clusterId: number, limit = 50) =>
  request<AssetListResponse>(`/api/faces/${clusterId}/assets${buildQuery({ limit })}`)

// ---------- Edit assistant ----------

export const planEdit = (intent: string, tripId?: number) =>
  request<EditPlan>('/api/edits/plan', {
    method: 'POST',
    body: JSON.stringify({ intent, trip_id: tripId }),
  })
export const getPlan = (planId: string) => request<EditPlan>(`/api/edits/plan/${planId}`)
export const updatePlan = (planId: string, clips: Clip[]) =>
  request<EditPlan>(`/api/edits/plan/${planId}`, {
    method: 'PUT',
    body: JSON.stringify({ clips }),
  })
export const approvePlan = (planId: string) =>
  request<{ ok: boolean; status: string }>(`/api/edits/plan/${planId}/approve`, { method: 'POST' })
export const planPackageUrl = (planId: string) => `${API_BASE}/api/edits/plan/${planId}/package`

export interface CreateStoryRequest {
  script: string
  voice_id?: string | null
  trip_id?: number | null
  ratio?: RenderRatio
  title?: string | null
  caption_style?: 'kinetic' | 'classic'
  cloud_approved: boolean
  voice_consent: boolean
  max_characters: number
}

export const createStory = (body: CreateStoryRequest) =>
  request<StoryJobResponse>('/api/edits/story', { method: 'POST', body: JSON.stringify(body) })

// ---------- Render ----------

export const startRender = (body: {
  plan_id: string
  ratio: RenderRatio
  music_path: string | null
  captions: boolean
  width: number
  height: number
  presets?: RenderRatio[]
  reframe?: 'smart' | 'center'
  safe_zones?: { top?: number; bottom?: number; right?: number }
  guide_overlay?: boolean
  music?: 'auto'
  music_auto?: boolean
}) => request<StartJobResponse>('/api/render', { method: 'POST', body: JSON.stringify(body) })

export const getRenderStatus = (jobId: string) => request<RenderStatus>(`/api/render/${jobId}`)

// ---------- NLE export ----------

export const exportPlan = (format: ExportFormat, planId: string) =>
  request<ExportResponse>(`/api/export/${format}`, { method: 'POST', body: JSON.stringify({ plan_id: planId }) })

// ---------- Touch-up ----------

export interface TouchupPreset {
  id: string
  label: string
}

export const TOUCHUP_PRESETS: TouchupPreset[] = [
  { id: 'auto_levels', label: 'Auto levels' },
  { id: 'white_balance', label: 'White balance' },
  { id: 'denoise', label: 'Denoise' },
  { id: 'sharpen', label: 'Sharpen' },
  { id: 'all', label: 'All (full pipeline)' },
]

export const applyTouchup = (asset_id: number, preset: string) =>
  request<StartJobResponse>('/api/touchup', {
    method: 'POST',
    body: JSON.stringify({ asset_id, preset }),
  })

export const touchupPreviewUrl = (asset_id: number, preset: string) =>
  `${API_BASE}/api/touchup/preview${buildQuery({ asset_id, preset })}`

export const listPlaces = () => request<Place[]>('/api/places')
export const listTrips = () => request<Trip[]>('/api/trips')
export const tripReel = (tripId: number, intent?: string) =>
  request<EditPlan>(`/api/trips/${tripId}/reel`, {
    method: 'POST',
    body: JSON.stringify({ intent: intent ?? null }),
  })

export const getDedupe = () => request<DedupeOut>('/api/dedupe')
export const resolveDedupe = (pair_id: string, action: DedupeAction) =>
  request<{ ok: boolean }>('/api/dedupe/resolve', {
    method: 'POST',
    body: JSON.stringify({ pair_id, action }),
  })

// ---------- Jobs ----------

export const listJobs = (limit = 20) =>
  request<JobListResponse>(`/api/jobs${buildQuery({ limit })}`)
export const getJob = (jobId: string) => request<Job>(`/api/jobs/${jobId}`)

// ---------- Exported files (render output, fcpxml/edl, touchup results) ----------
// The backend writes these under its exports dir; they are served statically at /exports/... .
export function exportedFileUrl(serverPath: string | null | undefined): string | null {
  if (!serverPath) return null
  const relative = serverPath.includes('/exports/') ? serverPath.split('/exports/').slice(1).join('/exports/') : serverPath.split('/').pop()
  if (!relative || relative.split('/').some((part) => part === '..')) return null
  return `${API_BASE}/exports/${relative.split('/').map(encodeURIComponent).join('/')}`
}
