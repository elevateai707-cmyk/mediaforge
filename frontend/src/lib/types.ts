// MediaForge API types — mirror of docs/API_CONTRACT.md

export interface Asset {
  id: number
  path: string
  kind: 'photo' | 'video'
  mime: string
  size: number
  width: number | null
  height: number | null
  duration: number | null
  taken_at: string | null
  added_at: string
  camera_make: string | null
  camera_model: string | null
  gps_lat: number | null
  gps_lon: number | null
  aesthetic_score: number | null
  caption: string | null
  status: string
  hash: string
  tags: string[]
  faces: string[]
  scene_count: number | null
  has_transcript: boolean
  city?: string | null
  region?: string | null
  country?: string | null
  place_name?: string | null
  trip_id?: number | null
}

export interface Place {
  city: string
  region: string | null
  count: number
  lat: number | null
  lon: number | null
}

export interface Trip {
  id: number
  title: string
  city: string | null
  region: string | null
  start_at: string | null
  end_at: string | null
  asset_count: number
  cover_asset_id: number | null
  lat: number | null
  lon: number | null
  radius_km: number | null
}

export interface DedupePair {
  id: string
  asset_a: number
  asset_b: number
  path_a: string
  path_b: string
  distance: number
  kind: string
}

export interface DedupeOut {
  exact: DedupePair[]
  near: DedupePair[]
}

export type DedupeAction = 'keep_a' | 'keep_b' | 'delete_b'

export interface ParsedIntent {
  place?: string | null
  ratio?: string | null
  platform?: string | null
  duration_s?: number | null
}

export interface AssetListResponse {
  total: number
  items: Asset[]
}

export interface SearchResponse {
  query: string
  results: Asset[]
  took_ms: number
}

export interface FaceCluster {
  id: number
  name: string | null
  count: number
  thumb_asset_id: number
}

export interface FacesResponse {
  clusters: FaceCluster[]
}

export interface TranscriptSegment {
  start: number
  end: number
  text: string
}

export interface TranscriptResponse {
  segments: TranscriptSegment[]
}

export interface SceneInfo {
  id: number
  start: number
  end: number
  caption: string
  aesthetic: number
}

export interface ScenesResponse {
  scenes: SceneInfo[]
}

export interface Clip {
  asset_id: number
  scene_id: number | null
  start: number
  end: number
  caption: string
  transition: string
  score: number
}

export interface EditPlan {
  plan_id: string
  status: 'draft' | 'approved' | string
  summary: string
  clips: Clip[]
  total_duration: number
  target_ratio: string
  parsed_intent?: ParsedIntent | null
  match_stats?: Record<string, unknown> | null
}

export interface Health {
  status: string
  gpu: 'cuda' | 'cpu'
  gpu_name: string | null
  vram_mb: number | null
  version: string
}

export interface AppConfig {
  use_cloud_llm: boolean
  media_dirs: string[]
  ollama_model: string
  resolve_available: boolean
  watcher_enabled?: boolean
  music_dir?: string
}

export interface Job {
  id: string
  kind: 'scan' | 'ai' | 'render' | 'touchup' | string
  status: 'running' | 'done' | 'error' | 'cancelled' | string
  progress: number
  message: string | null
  created_at: string
  finished_at: string | null
}

export interface JobListResponse {
  items: Job[]
}

export interface StartJobResponse {
  job_id: string
  kind: string
  plan_id?: string
}

export interface RenderStatus {
  status: 'done' | 'running' | 'error' | string
  output_path: string | null
  progress: number
}

export interface ExportResponse {
  ok?: boolean
  path?: string
  resolve?: string
  project?: string
}

export type RenderRatio = '9:16' | '1:1' | '16:9'

export const RENDER_RATIOS: RenderRatio[] = ['9:16', '1:1', '16:9']

export function ratioDimensions(ratio: RenderRatio): { width: number; height: number } {
  switch (ratio) {
    case '9:16':
      return { width: 1080, height: 1920 }
    case '1:1':
      return { width: 1080, height: 1080 }
    case '16:9':
      return { width: 1920, height: 1080 }
    default: {
      const _exhaustive: never = ratio
      return _exhaustive
    }
  }
}

// ---- WebSocket events (server → client) ----
export interface WsGpuEvent {
  type: 'gpu'
  mode: 'cuda' | 'cpu'
  vram_mb?: number
  warning?: string
}

export interface WsJobEvent {
  type: 'job'
  job_id: string
  kind: string
  status: 'running' | 'done' | 'error' | 'cancelled' | string
  progress: number
  message?: string
}

export interface WsJobBatchEvent {
  type: 'job.batch'
  job_id: string
  progress: number
  done: number
  total: number
}

export type WsEvent = WsGpuEvent | WsJobEvent | WsJobBatchEvent

export interface LiveJobState {
  job_id: string
  kind: string
  status: string
  progress: number
  message: string | null
  done: number | null
  total: number | null
  updated_at: number
}

export type ExportFormat = 'resolve' | 'fcpxml' | 'edl' | 'capcut'
