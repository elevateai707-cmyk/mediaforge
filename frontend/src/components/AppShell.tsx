import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Images,
  Search,
  Users,
  Clapperboard,
  Wand2,
  Settings,
  Cpu,
  Zap,
  TriangleAlert,
} from 'lucide-react'
import { useWsStore } from '@/lib/ws'
import { JobProgressPanel } from '@/components/JobProgressPanel'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: 'Library', icon: Images, end: true },
  { to: '/search', label: 'Search', icon: Search },
  { to: '/faces', label: 'Faces', icon: Users },
  { to: '/studio', label: 'Edit Studio', icon: Clapperboard },
  { to: '/touchup', label: 'Touch-up', icon: Wand2 },
  { to: '/settings', label: 'Settings', icon: Settings },
]

function GpuBadge() {
  const gpu = useWsStore((s) => s.gpu)
  const connected = useWsStore((s) => s.connected)
  if (!connected && !gpu) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground">
        <Cpu className="h-3.5 w-3.5" /> backend offline
      </div>
    )
  }
  const cuda = gpu?.mode === 'cuda'
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          className={cn(
            'flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium',
            cuda
              ? 'border-primary/40 bg-primary/10 text-primary'
              : 'border-destructive/40 bg-destructive/10 text-destructive',
          )}
        >
          {cuda ? <Zap className="h-3.5 w-3.5" /> : <TriangleAlert className="h-3.5 w-3.5" />}
          {cuda ? 'CUDA' : 'CPU'}
          {gpu?.vram_mb ? <span className="text-muted-foreground">{gpu.vram_mb}MB</span> : null}
        </div>
      </TooltipTrigger>
      <TooltipContent>
        {cuda
          ? `GPU acceleration active${gpu?.vram_mb ? ` · ${gpu.vram_mb} MB VRAM` : ''}`
          : gpu?.warning ?? 'Running on CPU — AI analysis will be slow.'}
      </TooltipContent>
    </Tooltip>
  )
}

function NavItems({ compact }: { compact?: boolean }) {
  return (
    <>
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} className="relative block">
          {({ isActive }) => (
            <span
              className={cn(
                'relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
                compact && 'justify-center px-0',
              )}
            >
              {isActive && (
                <motion.span
                  layoutId="nav-pill"
                  className="absolute inset-0 rounded-lg bg-gradient-to-r from-primary/25 to-secondary/25 border border-primary/30"
                  transition={{ type: 'spring', stiffness: 400, damping: 32 }}
                />
              )}
              <Icon className="relative z-10 h-4.5 w-4.5 shrink-0" />
              {!compact && <span className="relative z-10">{label}</span>}
            </span>
          )}
        </NavLink>
      ))}
    </>
  )
}

export function AppShell() {
  const location = useLocation()

  return (
    <div className="bg-grid min-h-screen">
      {/* ambient glows */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -top-40 left-1/4 h-96 w-96 rounded-full bg-primary/15 blur-[120px]" />
        <div className="absolute -bottom-40 right-1/4 h-96 w-96 rounded-full bg-secondary/10 blur-[120px]" />
      </div>

      {/* sidebar (md+) */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-border bg-background/70 backdrop-blur-xl md:flex">
        <div className="flex h-16 items-center gap-2.5 border-b border-border px-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-secondary shadow-[0_0_16px] shadow-primary/50">
            <Clapperboard className="h-4.5 w-4.5 text-white" />
          </div>
          <span className="text-lg font-bold tracking-tight">
            Media<span className="neon-text">Forge</span>
          </span>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-3">
          <NavItems />
        </nav>
        <div className="space-y-3 border-t border-border p-3">
          <GpuBadge />
          <p className="px-1 text-[10px] leading-relaxed text-muted-foreground/70">
            Local-first AI media library · backend :8420
          </p>
        </div>
      </aside>

      {/* mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-border bg-background/80 px-4 backdrop-blur-xl md:hidden">
        <div className="flex h-14 items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-secondary">
            <Clapperboard className="h-4 w-4 text-white" />
          </div>
          <span className="font-bold">
            Media<span className="neon-text">Forge</span>
          </span>
        </div>
        <GpuBadge />
      </header>
      <nav className="sticky top-14 z-30 overflow-x-auto border-b border-border bg-background/80 px-2 py-1.5 backdrop-blur-xl md:hidden">
        <div className="flex min-w-max gap-1">
          <NavItems compact />
        </div>
      </nav>

      {/* main content */}
      <main className="relative md:pl-60">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.25, ease: 'easeOut' }}
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </div>
      </main>

      <JobProgressPanel />
    </div>
  )
}
