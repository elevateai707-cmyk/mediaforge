import { useQuery } from '@tanstack/react-query'
import { editorRequest } from '@/lib/editor'

type Info = {version: string; revision: string; source_fingerprint: string; creative_profile: string; caption_presets: string[]; features: string[]; ffmpeg_available: boolean; missing_fonts: string[] | null; note: string}
export function BuildInfo() {
  const info = useQuery({queryKey: ['build-info'], queryFn: () => editorRequest<Info>('/api/build-info')})
  return <section className="glass mb-6 rounded-2xl p-4">
    <h2 className="font-semibold">Version & editing capabilities</h2>
    {info.isError && <p>Build details unavailable. Restart MediaForge after updating.</p>}
    {info.isLoading && <p>Checking this installation…</p>}
    {info.data && <div className="mt-2 space-y-2 text-sm">
      <p>MediaForge {info.data.version} · revision <code>{info.data.revision}</code></p>
      <p>Compare on both computers: <code>{info.data.source_fingerprint}</code> · creative profile {info.data.creative_profile}</p>
      <p>{info.data.caption_presets.length} caption presets · Creator Studio · repeat uploads · watched folders</p>
      <p className="text-muted-foreground">{info.data.note}</p>
      {!info.data.ffmpeg_available && <p className="text-destructive">FFmpeg is missing; rendering needs it installed.</p>}
      {info.data.missing_fonts === null ? <p>Font check unavailable on this system.</p> : info.data.missing_fonts.length > 0 && <p className="text-amber-400">Missing style fonts: {info.data.missing_fonts.join(', ')}. Install the style fonts before comparing rendered captions.</p>}
    </div>}
  </section>
}
