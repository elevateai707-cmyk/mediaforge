import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { activeJobs, onJobEvent, useWsStore } from '@/lib/ws'
import { Progress } from '@/components/ui/progress'
import { Badge } from '@/components/ui/badge'
import { toPercent } from '@/lib/utils'

const KIND_LABEL: Record<string, string> = {
  scan: 'Scanning',
  ai: 'AI analysis',
  render: 'Rendering',
  touchup: 'Touch-up',
}

export function JobProgressPanel() {
  const jobs = useWsStore((s) => s.jobs)
  const queryClient = useQueryClient()
  const [dismissed, setDismissed] = useState<Record<string, boolean>>({})

  const running = activeJobs(jobs)

  // Refetch data whenever a job reaches a terminal state
  useEffect(() => {
    return onJobEvent((job) => {
      if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') {
        queryClient.invalidateQueries()
      }
    })
  }, [queryClient])

  const visible = running.filter((j) => !dismissed[j.job_id])

  return (
    <div className="pointer-events-none fixed bottom-20 left-4 z-40 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2 md:bottom-4 md:left-[calc(15rem+1rem)]">
      <AnimatePresence>
        {visible.map((job) => (
          <motion.div
            key={job.job_id}
            initial={{ opacity: 0, x: 40 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 40 }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            className="glass-strong pointer-events-auto rounded-xl p-3 shadow-2xl shadow-black/50"
          >
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2">
                <Badge variant="default">{KIND_LABEL[job.kind] ?? job.kind}</Badge>
                <span className="truncate text-xs text-muted-foreground">{job.job_id.slice(0, 12)}</span>
              </div>
              <button
                onClick={() => setDismissed((d) => ({ ...d, [job.job_id]: true }))}
                className="rounded p-0.5 text-muted-foreground hover:bg-white/5 hover:text-foreground cursor-pointer"
                aria-label="Dismiss job panel"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <Progress value={toPercent(job.progress)} />
            <p className="mt-1.5 truncate text-[11px] text-muted-foreground">
              {job.message ?? `${Math.round(toPercent(job.progress))}%`}
              {job.total !== null && job.total !== undefined && job.done !== null && job.done !== undefined
                ? ` — ${job.done}/${job.total} files`
                : ''}
            </p>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
