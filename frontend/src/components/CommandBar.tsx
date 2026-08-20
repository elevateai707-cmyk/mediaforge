import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clapperboard, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'

const EXAMPLES = [
  'make a highlight reel for tiktok of my trip to edmonton 9:16',
  'beach sunset clips with Kaleb talking',
  '60-second vancouver recap 16:9',
]

function looksLikeEdit(q: string): boolean {
  return /reel|tiktok|highlight|recap|9:16|16:9|edit|cut|studio/i.test(q)
}

export function CommandBar() {
  const navigate = useNavigate()
  const [value, setValue] = useState('')

  function submit(raw?: string) {
    const q = (raw ?? value).trim()
    if (!q) return
    if (looksLikeEdit(q)) navigate('/studio', { state: { intent: q } })
    else navigate(`/search?q=${encodeURIComponent(q)}`)
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    submit()
  }

  return (
    <form onSubmit={onSubmit} className="glass mb-6 rounded-2xl p-3 sm:p-4">
      <label htmlFor="mf-command" className="sr-only">
        Command bar
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          id="mf-command"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Ask for a reel or search — e.g. make a highlight reel for tiktok of my trip to edmonton 9:16"
          className="h-12 min-w-0 flex-1 rounded-xl border border-border bg-background/50 px-4 text-sm outline-none placeholder:text-muted-foreground/70 focus-visible:ring-2 focus-visible:ring-ring"
        />
        <div className="flex gap-2">
          <Button type="submit" variant="gradient" className="h-12 flex-1 sm:w-36">
            {looksLikeEdit(value) ? <Clapperboard className="h-4 w-4" /> : <Search className="h-4 w-4" />}
            {looksLikeEdit(value) ? 'Plan reel' : 'Search'}
          </Button>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => {
              setValue(ex)
              submit(ex)
            }}
            className="cursor-pointer rounded-full border border-border px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary"
          >
            {ex}
          </button>
        ))}
      </div>
    </form>
  )
}
