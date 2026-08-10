import { create } from 'zustand'
import { API_BASE } from './api'
import type { LiveJobState, WsEvent } from './types'

export interface GpuInfo {
  mode: 'cuda' | 'cpu'
  vram_mb: number | null
  warning: string | null
}

interface WsStore {
  connected: boolean
  gpu: GpuInfo | null
  jobs: Record<string, LiveJobState>
  upsertJob: (job: LiveJobState) => void
  setConnected: (v: boolean) => void
  setGpu: (g: GpuInfo) => void
}

export const useWsStore = create<WsStore>((set) => ({
  connected: false,
  gpu: null,
  jobs: {},
  upsertJob: (job) =>
    set((s) => ({ jobs: { ...s.jobs, [job.job_id]: { ...s.jobs[job.job_id], ...job } } })),
  setConnected: (v) => set({ connected: v }),
  setGpu: (g) => set({ gpu: g }),
}))

export function activeJobs(jobs: Record<string, LiveJobState>): LiveJobState[] {
  return Object.values(jobs)
    .filter((j) => j.status === 'running' || j.status === 'queued' || j.status === 'pending')
    .sort((a, b) => b.updated_at - a.updated_at)
}

// Tiny event emitter so pages can refetch when a job reaches a terminal state.
type JobListener = (job: LiveJobState) => void
const listeners = new Set<JobListener>()

export function onJobEvent(fn: JobListener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function emit(job: LiveJobState) {
  listeners.forEach((fn) => {
    try {
      fn(job)
    } catch {
      // listener errors must not break the socket loop
    }
  })
}

let socket: WebSocket | null = null
let retryDelay = 1000
let manuallyClosed = false

function wsBase(): string {
  if (API_BASE) {
    // absolute API base (e.g. http://localhost:8420) → derive ws url
    return API_BASE.replace(/^http/, 'ws')
  }
  // proxied through Vite: same host, /ws path
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`
}

function handleEvent(ev: WsEvent) {
  const store = useWsStore.getState()
  if (ev.type === 'gpu') {
    store.setGpu({ mode: ev.mode, vram_mb: ev.vram_mb ?? null, warning: ev.warning ?? null })
    return
  }
  if (ev.type === 'job') {
    const job: LiveJobState = {
      job_id: ev.job_id,
      kind: ev.kind,
      status: ev.status,
      progress: ev.progress ?? 0,
      message: ev.message ?? null,
      done: null,
      total: null,
      updated_at: Date.now(),
    }
    store.upsertJob(job)
    emit(job)
    return
  }
  if (ev.type === 'job.batch') {
    const prev = store.jobs[ev.job_id]
    const job: LiveJobState = {
      job_id: ev.job_id,
      kind: prev?.kind ?? 'scan',
      status: prev?.status ?? 'running',
      progress: ev.total > 0 ? ev.done / ev.total : ev.progress,
      message: prev?.message ?? null,
      done: ev.done,
      total: ev.total,
      updated_at: Date.now(),
    }
    store.upsertJob(job)
    emit(job)
  }
}

export function connectWs(): void {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return
  }
  manuallyClosed = false
  const url = `${wsBase()}/ws`
  try {
    socket = new WebSocket(url)
  } catch {
    scheduleReconnect()
    return
  }

  socket.onopen = () => {
    retryDelay = 1000
    useWsStore.getState().setConnected(true)
  }

  socket.onmessage = (msg) => {
    try {
      const data = JSON.parse(String(msg.data)) as WsEvent
      handleEvent(data)
    } catch {
      // ignore non-JSON frames
    }
  }

  socket.onclose = () => {
    useWsStore.getState().setConnected(false)
    socket = null
    if (!manuallyClosed) scheduleReconnect()
  }

  socket.onerror = () => {
    // onclose follows; reconnect handled there
  }
}

function scheduleReconnect() {
  const delay = retryDelay
  retryDelay = Math.min(retryDelay * 2, 30000)
  window.setTimeout(() => {
    if (!manuallyClosed) connectWs()
  }, delay)
}

export function closeWs() {
  manuallyClosed = true
  socket?.close()
  socket = null
}
