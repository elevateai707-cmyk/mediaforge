import { useState } from 'react'
import { Download, FolderOutput, Loader2 } from 'lucide-react'
import { downloadUrl, exportMediaCopy, triggerDownload, zipExportUrl } from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatBytes } from '@/lib/utils'

interface ExportDialogProps {
  ids: number[]
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** Export media originals: browser download (single file or streamed zip) or copy to a server folder. */
export function ExportDialog({ ids, open, onOpenChange }: ExportDialogProps) {
  const [sidecars, setSidecars] = useState(true)
  const [dest, setDest] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const many = ids.length > 1

  const download = () => {
    triggerDownload(many ? zipExportUrl(ids, sidecars) : downloadUrl(ids[0]))
    setMsg({
      ok: true,
      text: many ? 'Zip is streaming to your browser downloads.' : 'Download started.',
    })
  }

  const copy = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const res = await exportMediaCopy(ids, dest.trim() || null, sidecars)
      setMsg({
        ok: true,
        text: `Copying ${res.files} files (${formatBytes(res.bytes)}) to ${res.dest} — watch the job tray.`,
      })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) setMsg(null)
        onOpenChange(o)
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Export {ids.length} {ids.length === 1 ? 'item' : 'items'}</DialogTitle>
          <DialogDescription>Original files, untouched — not an edit plan.</DialogDescription>
        </DialogHeader>

        <label className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.03] px-3 py-2 text-sm">
          <span>
            Include companion files
            <span className="block text-xs text-muted-foreground">.SRT, .DNG, … sharing the same name</span>
          </span>
          <Switch checked={sidecars} onCheckedChange={setSidecars} />
        </label>

        <Tabs defaultValue="download">
          <TabsList>
            <TabsTrigger value="download">Download</TabsTrigger>
            <TabsTrigger value="copy">Copy to server folder</TabsTrigger>
          </TabsList>
          <TabsContent value="download" className="space-y-3">
            <p className="text-xs text-muted-foreground">
              {many
                ? 'Several files are streamed as an uncompressed zip (with companion files if enabled) — nothing is staged on disk.'
                : 'The original file is streamed to your browser. Companion files are included with the zip and folder copy.'}
            </p>
            <Button onClick={download}>
              <Download /> {many ? 'Download zip' : 'Download original'}
            </Button>
          </TabsContent>
          <TabsContent value="copy" className="space-y-3">
            <Input
              placeholder="/srv/iris/media/mediaforge/exports/media/<timestamp>/"
              value={dest}
              onChange={(e) => setDest(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Absolute folder on the G15, outside the library. Leave blank for a timestamped default.
            </p>
            <Button onClick={() => void copy()} disabled={busy}>
              {busy ? <Loader2 className="animate-spin" /> : <FolderOutput />} Copy files
            </Button>
          </TabsContent>
        </Tabs>

        {msg && <p className={`text-xs ${msg.ok ? 'text-muted-foreground' : 'text-destructive'}`}>{msg.text}</p>}
      </DialogContent>
    </Dialog>
  )
}
