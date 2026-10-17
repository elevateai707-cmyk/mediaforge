import { editorRequest } from './editor'

export const creatorRequest = <T,>(path: string, body?: unknown) =>
  editorRequest<T>('/api/creator' + path, body === undefined ? 'GET' : 'POST', body)

export interface Analysis {
  asset_id: number; status: string; name?: string; duration?: number
  coverage?: { samples: number; visual_samples: number; scene_ranges: number; method: string }
  moments?: { start: number; end: number; description: string; suggestion: string; sample_at: number }[]
  transcript?: { start: number; end: number; text: string }[]
  audio_status?: string; edit_suggestions?: string[]; warnings?: string[]
}
export interface Brief {
  id: string; source: string; notice: string | null; created_at: string
  camera: { name: string; settings: string; source: string | null }
  creative: {
    title: string; hooks: { text: string; why: string }[]; script: string
    shots: { shot: string; filming: string; say: string; seconds: number }[]
    edit_notes: string[]; broll_prompts: string[]; post_caption: string
  }
}
export interface CreatorJob {
  id: string; kind: string; status: string; progress: number; message: string
  meta?: { result?: Brief | { completed: { asset_id: number; status: string }[]; errors: { asset_id: number }[] } }
}
export interface BrollPlan { plan_id: string; title: string; duration: number; revision: number }
