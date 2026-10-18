import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, FolderInput, Loader2, UploadCloud, X, XCircle } from 'lucide-react'
import {
  importFolder,
  scanPaths,
  uploadFile,
} from '@/lib/api'
import { IMPORT_OPEN_EVENT, formatBytes, toPercent } from '@/lib/utils'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

interface UploadItem {
  id: number
  file: File
  status: 'queued' | 'uploading' | 'done' | 'error'
  loaded: number
  error?: string
  folder?: string
}

let nextId = 1

/**
 * Global Import dialog: browser upload (multi-file, drag-and-drop) and
 * server-side folder import. Opened via IMPORT_OPEN_EVENT; stays mounted in
 * AppShell so uploads keep running while the dialog is closed.
 */
export function ImportDialog() {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<UploadItem[]>([])
  const [dragging, setDragging] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [folderPath, setFolderPath] = useState('')
  const [folderBusy, setFolderBusy] = useState(false)
  const [folderMsg, setFolderMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const running = useRef(false)
  const queue = useRef<UploadItem[]>([])
  const folders = useRef(new Set<string>())

  useEffect(() => {
    const onOpen = () => setOpen(true)
    // Dragging files anywhere over the app opens the dialog so a stray drop
    // never navigates the browser away from MediaForge.
    const onDragEnter = (e: DragEvent) => {
      if (e.dataTransfer?.types?.includes('Files')) setOpen(true)
    }
    const stop = (e: DragEvent) => {
      if (e.dataTransfer?.types?.includes('Files')) e.preventDefault()
    }
    window.addEventListener(IMPORT_OPEN_EVENT, onOpen)
    window.addEventListener('dragenter', onDragEnter)
    window.addEventListener('dragover', stop)
    window.addEventListener('drop', stop)
    return () => {
      window.removeEventListener(IMPORT_OPEN_EVENT, onOpen)
      window.removeEventListener('dragenter', onDragEnter)
      window.removeEventListener('dragover', stop)
      window.removeEventListener('drop', stop)
    }
  }, [])

  const patch = useCallback((id: number, p: Partial<UploadItem>) => {
    setItems((list) => list.map((it) => (it.id === id ? { ...it, ...p } : it)))
  }, [])

  const pump = useCallback(async () => {
    if (running.current) return
    running.current = true
    try {
      let next: UploadItem | undefined
      while ((next = queue.current.shift())) {
        const it = next
        patch(it.id, { status: 'uploading', loaded: 0 })
        try {
          const { promise } = uploadFile(it.file, (loaded) => patch(it.id, { loaded }))
          const res = await promise
          folders.current.add(res.folder)
          patch(it.id, { status: 'done', loaded: it.file.size, folder: res.folder })
        } catch (err) {
          patch(it.id, { status: 'error', error: err instanceof Error ? err.message : String(err) })
        }
      }
      // Queue drained: scan every folder we wrote into (shows in the job tray).
      const toScan = [...folders.current]
      folders.current.clear()
      if (toScan.length > 0) {
        try {
          await scanPaths(toScan)
          setNote('Upload finished — scanning the new media now. Watch the job tray for progress.')
        } catch (err) {
          setNote(`Upload finished but the scan failed to start: ${err instanceof Error ? err.message : err}`)
        }
      }
    } finally {
      running.current = false
    }
  }, [patch])

  const addFiles = useCallback(
    (files: FileList | File[]) => {
      const fresh: UploadItem[] = Array.from(files).map((file) => ({
        id: nextId++,
        file,
        status: 'queued',
        loaded: 0,
      }))
      if (fresh.length === 0) return
      setNote(null)
      setItems((list) => [...list, ...fresh])
      queue.current.push(...fresh)
      void pump()
    },
    [pump],
  )

  const startFolder = async () => {
    const path = folderPath.trim()
    if (!path) return
    setFolderBusy(true)
    setFolderMsg(null)
    try {
      const res = await importFolder(path)
      setFolderMsg({
        ok: true,
        text: `Copying ${res.files.toLocaleString()} files (${formatBytes(res.bytes)}) to ${res.dest}. A scan starts automatically when the copy finishes — watch the job tray.`,
      })
      setFolderPath('')
    } catch (err) {
      setFolderMsg({ ok: false, text: err instanceof Error ? err.message : String(err) })
    } finally {
      setFolderBusy(false)
    }
  }

  const finished = items.filter((i) => i.status === 'done' || i.status === 'error')

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Import media</DialogTitle>
          <DialogDescription>
            Bring photos and video into the library. New files are scanned automatically.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="upload">
          <TabsList>
            <TabsTrigger value="upload">Upload files</TabsTrigger>
            <TabsTrigger value="folder">Folder on this server</TabsTrigger>
          </TabsList>

          <TabsContent value="upload" className="space-y-3">
            <div
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files)
              }}
              onClick={() => fileInput.current?.click()}
              className={`flex cursor-pointer flex-col items-center gap-2 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
                dragging ? 'border-primary bg-primary/10' : 'border-border hover:border-primary/50'
              }`}
            >
              <UploadCloud className="h-8 w-8 text-primary" />
              <p className="text-sm font-medium">Drop files here or click to choose</p>
              <p className="text-xs text-muted-foreground">
                Video, photos and companion files (.SRT, .DNG, …). Large files stream straight to disk.
              </p>
              <input
                ref={fileInput}
                type="file"
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files) addFiles(e.target.files)
                  e.target.value = ''
                }}
              />
            </div>

            {items.length > 0 && (
              <div className="max-h-56 space-y-2 overflow-y-auto pr-1">
                {items.map((it) => (
                  <div key={it.id} className="rounded-lg bg-white/[0.03] p-2.5 text-xs">
                    <div className="mb-1 flex items-center gap-2">
                      {it.status === 'done' ? (
                        <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-primary" />
                      ) : it.status === 'error' ? (
                        <XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
                      ) : it.status === 'uploading' ? (
                        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
                      ) : (
                        <span className="h-3.5 w-3.5 shrink-0" />
                      )}
                      <span className="min-w-0 flex-1 truncate">{it.file.name}</span>
                      <span className="shrink-0 tabular text-muted-foreground">{formatBytes(it.file.size)}</span>
                    </div>
                    {it.status === 'error' ? (
                      <p className="text-destructive">{it.error}</p>
                    ) : (
                      <Progress value={toPercent(it.file.size ? it.loaded / it.file.size : 0)} />
                    )}
                  </div>
                ))}
              </div>
            )}
            {finished.length > 0 && finished.length === items.length && (
              <Button variant="ghost" size="sm" onClick={() => setItems([])}>
                <X className="h-3.5 w-3.5" /> Clear list
              </Button>
            )}
            {note && <p className="text-xs text-muted-foreground">{note}</p>}
          </TabsContent>

          <TabsContent value="folder" className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Type a folder path on the G15 (e.g. <code>/home/g15/Videos/shoot</code>). It is copied into the
              library as a background job — your original is left untouched — then scanned.
            </p>
            <div className="flex gap-2">
              <Input
                placeholder="/home/g15/Videos/my-folder"
                value={folderPath}
                onChange={(e) => setFolderPath(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && void startFolder()}
              />
              <Button onClick={() => void startFolder()} disabled={folderBusy || !folderPath.trim()}>
                {folderBusy ? <Loader2 className="animate-spin" /> : <FolderInput />} Import
              </Button>
            </div>
            {folderMsg && (
              <p className={`text-xs ${folderMsg.ok ? 'text-muted-foreground' : 'text-destructive'}`}>
                {folderMsg.text}
              </p>
            )}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
