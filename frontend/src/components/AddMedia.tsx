import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { FolderPlus, Upload } from 'lucide-react'
import { API_BASE } from '@/lib/api'
import { Button } from '@/components/ui/button'

export function AddMedia() {
  const input = useRef<HTMLInputElement>(null)
  const cache = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [errors, setErrors] = useState<string[]>([])
  async function upload(files: File[]) {
    if (!files.length || busy) return
    setBusy(true); setErrors([])
    let completed = 0
    const failed: string[] = []
    try {
      for (const [i, file] of files.entries()) {
        setStatus(`Adding ${i + 1} of ${files.length}: ${file.name}`)
        try {
          const response = await fetch(`${API_BASE}/api/creator/upload?filename=${encodeURIComponent(file.name)}`, {
            method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
          })
          const data = await response.json()
          if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Upload failed')
          completed++
          await cache.invalidateQueries({ queryKey: ['assets'] })
          await cache.invalidateQueries({ queryKey: ['creator-assets'] })
        } catch (e) { failed.push(`${file.name}: ${e instanceof Error ? e.message : 'Upload failed'}`) }
      }
      setStatus(`${completed} of ${files.length} files added or already in your library. You can add more anytime.`)
      setErrors(failed)
    } finally { setBusy(false) }
  }
  return <section className="glass mb-6 rounded-2xl p-4" aria-label="Add media">
    <div className="flex flex-wrap items-center gap-3">
      <Button onClick={() => input.current?.click()} disabled={busy}><Upload className="h-4 w-4" />{busy ? 'Adding media…' : 'Add photos & videos'}</Button>
      <Button variant="outline" asChild><Link to="/settings"><FolderPlus className="h-4 w-4" />Watch folders</Link></Button>
      <input ref={input} className="hidden" type="file" accept="image/*,video/*" multiple onChange={e => { void upload(Array.from(e.target.files || [])); e.target.value = '' }} />
    </div>
    <p className="mt-3 text-sm text-muted-foreground">Upload from this device, or save a folder in Settings and enable Folder watcher. New files appear while MediaForge is running; scan again to catch files added while it was closed.</p>
    {status && <p role="status" className="mt-2 text-sm">{status}</p>}
    {errors.length > 0 && <ul role="alert" className="mt-2 text-sm text-destructive">{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>}
  </section>
}
