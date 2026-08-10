import type {
  AppConfig,
  Asset,
  AssetListResponse,
  Clip,
  EditPlan,
  ExportFormat,
  ExportResponse,
  FacesResponse,
  Health,
  Job,
  JobListResponse,
  RenderRatio,
  RenderStatus,
  SceneInfo,
  ScenesResponse,
  SearchResponse,
  StartJobResponse,
  TranscriptResponse,
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

// ---------- Scan & ingest ----------

export const scanPaths = (paths: string[]) =>
  request<StartJobResponse>('/api/scan', { method: 'POST', body: JSON.stringify({ paths }) })
export const rescanPaths = (paths: string[]) =>
  request<StartJobResponse>('/api/rescan', { method: 'POST', body: JSON.stringify({ paths }) })
export const cancelScan = (job_id: string) =>
  request<{ ok: boolean }>('/api/scan/cancel', { method: 'POST', body: JSON.stringify({ job_id }) })

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
}

export const listAssets = (params: AssetQueryParams = {}) =>
  request<AssetListResponse>(`/api/assets${buildQuery({ limit: 50, ...params })}`)

export const getAsset = (id: number) => request<Asset>(`/api/assets/${id}`)
export const getTranscript = (id: number) =>
  request<TranscriptResponse>(`/api/assets/${id}/transcript`)
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

export const planEdit = (intent: string) =>
  request<EditPlan>('/api/edits/plan', { method: 'POST', body: JSON.stringify({ intent }) })
export const getPlan = (planId: string) => request<EditPlan>(`/api/edits/plan/${planId}`)
export const updatePlan = (planId: string, clips: Clip[]) =>
  request<EditPlan>(`/api/edits/plan/${planId}`, {
    method: 'PUT',
    body: JSON.stringify({ clips }),
  })
export const approvePlan = (planId: string) =>
  request<{ ok: boolean; status: string }>(`/api/edits/plan/${planId}/approve`, { method: 'POST' })

// ---------- Render ----------

export const startRender = (body: {
  plan_id: string
  ratio: RenderRatio
  music_path: string | null
  captions: boolean
  width: number
  height: number
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

// ---------- Jobs ----------

export const listJobs = (limit = 20) =>
  request<JobListResponse>(`/api/jobs${buildQuery({ limit })}`)
export const getJob = (jobId: string) => request<Job>(`/api/jobs/${jobId}`)

// ---------- Exported files (render output, fcpxml/edl, touchup results) ----------
// The backend writes these under its exports dir; they are served statically at /exports/... .
export function exportedFileUrl(serverPath: string | null | undefined): string | null {
  if (!serverPath) return null
  const filename = serverPath.split('/').pop()
  if (!filename) return null
  return `${API_BASE}/exports/${encodeURIComponent(filename)}`
}
