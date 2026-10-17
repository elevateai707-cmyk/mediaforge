import { createContext, useCallback, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Clapperboard, Loader2, LogIn } from 'lucide-react'
import { API_BASE } from '@/lib/api'
import { closeWs, connectWs, useWsStore } from '@/lib/ws'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

type Session = { ok: boolean; enabled: boolean }
type AuthState = 'checking' | 'authorized' | 'login' | 'offline'

type AuthContextValue = {
  authEnabled: boolean
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuthGate(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuthGate must be used inside AuthGate')
  return value
}

async function readSession(): Promise<{ status: number; session?: Session }> {
  const response = await fetch(`${API_BASE}/api/auth/session`, { credentials: 'include' })
  if (response.status === 401) return { status: 401 }
  if (!response.ok) throw new Error(`Session check failed (${response.status})`)
  return { status: response.status, session: await response.json() as Session }
}

export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [state, setState] = useState<AuthState>('checking')
  const [authEnabled, setAuthEnabled] = useState(false)
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const sessionRequest = useRef(0)

  const clearPrivateState = useCallback(() => {
    void queryClient.cancelQueries().finally(() => queryClient.clear())
    closeWs()
    useWsStore.setState({ connected: false, gpu: null, jobs: {} })
  }, [queryClient])

  const probe = useCallback(async () => {
    const request = ++sessionRequest.current
    setState('checking')
    setError(null)
    try {
      const result = await readSession()
      if (request !== sessionRequest.current) return
      if (result.status === 401) {
        clearPrivateState()
        setAuthEnabled(true)
        setState('login')
        return
      }
      setAuthEnabled(Boolean(result.session?.enabled))
      setState('authorized')
    } catch {
      if (request !== sessionRequest.current) return
      clearPrivateState()
      setState('offline')
      setError('Cannot reach MediaForge. Check the backend connection and retry.')
    }
  }, [clearPrivateState])

  useEffect(() => {
    void probe()
    return () => { sessionRequest.current += 1 }
  }, [probe])

  useEffect(() => {
    if (state !== 'authorized') return
    connectWs()
    return () => closeWs()
  }, [state])

  useEffect(() => {
    const onAuthRequired = () => {
      sessionRequest.current += 1
      clearPrivateState()
      setAuthEnabled(true)
      setToken('')
      setError('Your session expired. Sign in again to continue.')
      setState('login')
    }
    window.addEventListener('mf-auth-required', onAuthRequired)
    return () => window.removeEventListener('mf-auth-required', onAuthRequired)
  }, [clearPrivateState])

  const login = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    try {
      const response = await fetch(`${API_BASE}/api/auth/login`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      })
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: unknown } | null
        throw new Error(typeof body?.detail === 'string' ? body.detail : 'Sign-in failed.')
      }
      setToken('')
      await probe()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Sign-in failed.')
    }
  }

  const logout = useCallback(async () => {
    sessionRequest.current += 1
    clearPrivateState()
    setToken('')
    setState('login')
    try {
      const response = await fetch(`${API_BASE}/api/auth/logout`, { method: 'POST', credentials: 'include' })
      if (!response.ok && response.status !== 401) throw new Error('Logout failed')
      setError(null)
    } catch {
      setError('Private content is hidden, but the server could not revoke this session. Reconnect and sign out again.')
    }
  }, [clearPrivateState])

  if (state === 'authorized') {
    return <AuthContext.Provider value={{ authEnabled, logout }}>{children}</AuthContext.Provider>
  }

  if (state === 'checking') {
    return <GateFrame><Loader2 className="h-6 w-6 animate-spin text-primary" /><p>Checking MediaForge session…</p></GateFrame>
  }

  if (state === 'offline') {
    return <GateFrame><p className="text-destructive">{error}</p><Button onClick={() => void probe()}>Retry</Button></GateFrame>
  }

  return (
    <GateFrame>
      <form className="w-full max-w-sm space-y-4" onSubmit={login}>
        <div><h1 className="font-display text-2xl font-semibold">Sign in to MediaForge</h1><p className="mt-1 text-sm text-muted-foreground">Enter the access token for this server.</p></div>
        <Input type="password" autoComplete="current-password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Access token" required autoFocus />
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <Button className="w-full" type="submit"><LogIn /> Sign in</Button>
        <Button className="w-full" variant="ghost" type="button" onClick={() => void probe()}>Retry session check</Button>
      </form>
    </GateFrame>
  )
}

function GateFrame({ children }: { children: ReactNode }) {
  return <main className="bg-grid flex min-h-screen items-center justify-center p-6 text-center"><div className="glass flex w-full max-w-md flex-col items-center gap-4 rounded-2xl p-8"><div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-secondary"><Clapperboard className="h-5 w-5 text-primary-foreground" /></div>{children}</div></main>
}
