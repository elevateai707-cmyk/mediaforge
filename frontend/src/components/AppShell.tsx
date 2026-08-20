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
  HelpCircle,
  MapPin,
  Copy,
} from 'lucide-react'
import { useWsStore } from '@/lib/ws'
import { HELP_OPEN_EVENT } from '@/lib/help-kb'
import { JobProgressPanel } from '@/components/JobProgressPanel'
import { HelpChat } from '@/components/HelpChat'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

interface NavItem {
  to: string
  label: string
  icon: typeof Images
  end?: boolean
}

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: 'Browse',
    items: [
      { to: '/', label: 'Library', icon: Images, end: true },
      { to: '/search', label: 'Search', icon: Search },
      { to: '/faces', label: 'Faces', icon: Users },
      { to: '/map', label: 'Map', icon: MapPin },
    ],
  },
  {
    label: 'Create',
    items: [
      { to: '/studio', label: 'Studio', icon: Clapperboard },
      { to: '/touchup', label: 'Touch-up', icon: Wand2 },
    ],
  },
  {
    label: 'System',
    items: [
      { to: '/dedupe', label: 'Duplicates', icon: Copy },
      { to: '/settings', label: 'Settings', icon: Settings },
    ],
  },
]

const MOBILE_NAV: NavItem[] = [
  { to: '/', label: 'Library', icon: Images, end: true },
  { to: '/search', label: 'Search', icon: Search },
  { to: '/studio', label: 'Studio', icon: Clapperboard },
  { to: '/faces', label: 'Faces', icon: Users },
  { to: '/settings', label: 'More', icon: Settings },
]

function GpuBadge() {
  const gpu = useWsStore((s) => s.gpu)
  const connected = useWsStore((s) => s.connected)
  if (!connected && !gpu) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-border px-3 py-2 text-xs text-muted-foreground">
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
            'flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-medium tabular',
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

function BrandMark({ compact }: { compact?: boolean }) {
  return (
    <NavLink to="/" className="flex items-center gap-2.5">
      <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-secondary shadow-[0_0_18px] shadow-primary/40">
        <Clapperboard className="h-4 w-4 text-primary-foreground" />
      </div>
      {!compact && (
        <span className="font-display text-lg font-semibold tracking-tight">
          Media<span className="neon-text">Forge</span>
        </span>
      )}
    </NavLink>
  )
}

export function AppShell() {
  const location = useLocation()

  return (
    <div className="bg-grid min-h-screen">
      <div className="grain" aria-hidden />
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -top-40 left-[12%] h-96 w-96 rounded-full bg-primary/12 blur-[120px]" />
        <div className="absolute -bottom-40 right-[8%] h-96 w-96 rounded-full bg-secondary/10 blur-[120px]" />
      </div>

      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-border bg-background/75 backdrop-blur-xl md:flex">
        <div className="flex h-16 items-center gap-2 border-b border-border pr-4">
          <div className="sprocket-rail h-16 w-4 shrink-0 opacity-70" aria-hidden />
          <BrandMark />
        </div>
        <nav className="flex-1 space-y-5 overflow-y-auto p-3">
          {NAV_GROUPS.map((group) => (
            <div key={group.label}>
              <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/80">
                {group.label}
              </p>
              <div className="space-y-0.5">
                {group.items.map(({ to, label, icon: Icon, end }) => (
                  <NavLink key={to} to={to} end={end} className="relative block">
                    {({ isActive }) => (
                      <span
                        className={cn(
                          'relative flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition-colors',
                          isActive
                            ? 'text-foreground'
                            : 'text-muted-foreground hover:text-foreground',
                        )}
                      >
                        {isActive && (
                          <motion.span
                            layoutId="nav-pill"
                            className="absolute inset-0 rounded-xl border border-primary/30 bg-gradient-to-r from-primary/20 to-secondary/15"
                            transition={{ type: 'spring', stiffness: 400, damping: 32 }}
                          />
                        )}
                        <Icon className="relative z-10 h-4 w-4 shrink-0" />
                        <span className="relative z-10">{label}</span>
                      </span>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className="space-y-2 border-t border-border p-3">
          <GpuBadge />
          <button
            type="button"
            onClick={() => window.dispatchEvent(new CustomEvent(HELP_OPEN_EVENT))}
            className="flex h-10 w-full items-center gap-2 rounded-xl px-3 text-sm text-muted-foreground transition-colors hover:bg-white/5 hover:text-foreground"
          >
            <HelpCircle className="h-4 w-4" /> Ask Forge
            <kbd className="ml-auto rounded-md border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
              ?
            </kbd>
          </button>
          <p className="px-1 text-[10px] leading-relaxed text-muted-foreground/70">
            Local-first cutting room · :8420
          </p>
        </div>
      </aside>

      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-border bg-background/80 px-4 backdrop-blur-xl md:hidden">
        <div className="flex h-14 items-center gap-2">
          <BrandMark />
        </div>
        <GpuBadge />
      </header>

      <main className="relative pb-24 md:pb-8 md:pl-60">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.22, ease: [0.2, 0, 0, 1] }}
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </div>
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-background/90 px-1 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl md:hidden">
        <div className="grid grid-cols-5">
          {MOBILE_NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className="flex min-h-14 flex-col items-center justify-center gap-0.5 text-[10px]"
            >
              {({ isActive }) => (
                <>
                  <Icon className={cn('h-5 w-5', isActive ? 'text-primary' : 'text-muted-foreground')} />
                  <span className={cn(isActive ? 'text-primary' : 'text-muted-foreground')}>{label}</span>
                </>
              )}
            </NavLink>
          ))}
        </div>
      </nav>

      <JobProgressPanel />
      <HelpChat />
    </div>
  )
}
