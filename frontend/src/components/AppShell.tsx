import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { BookOpen, Search, FolderTree, BarChart3, Brain, Settings, RefreshCw } from 'lucide-react'
import { useApi } from '../useApi'
import { getAppConfig, getHealth } from '../api'
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSessionProvider } from '../context/SessionProviderContext'

const navItems = [
  { to: '/library', label: 'Library', icon: BookOpen },
  { to: '/search', label: 'Search', icon: Search },
  { to: '/explorer', label: 'Explorer', icon: FolderTree },
  { to: '/insights', label: 'Insights', icon: BarChart3 },
  { to: '/settings', label: 'Settings', icon: Settings },
] as const

export function AppShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { weeklyCost } = useSessionProvider()

  const isSyncing =
    (queryClient.getQueryData<{ split_brain_sync_status?: string }>(['health'])
      ?.split_brain_sync_status) === 'syncing'

  const [isGlobalStreamActive, setIsGlobalStreamActive] = useState(false)
  useEffect(() => {
    const handler = (e: any) => setIsGlobalStreamActive(e.detail)
    globalThis.addEventListener('stream-activity', handler)
    return () => globalThis.removeEventListener('stream-activity', handler)
  }, [])

  // Poll faster while a sync is in progress so the banner dismisses quickly,
  // but drop to a slow failsafe rate if an SSE stream is active to prevent network starvation.
  const { data: health } = useApi(getHealth, {
    cacheKey: 'health',
    refetchInterval: isGlobalStreamActive ? 120_000 : (isSyncing ? 5_000 : 60_000),
  })
  const { data: appConfig } = useApi(getAppConfig, { 
    cacheKey: 'app-config', 
    refetchInterval: isGlobalStreamActive ? 120_000 : 60_000 
  })

  const syncStatus = health?.split_brain_sync_status

  // P2-1: Refresh auth status when Tauri window regains focus.
  // This handles the case where user completes Google OAuth in system browser and returns.
  useEffect(() => {
    let cleanup: (() => void) | undefined
    // Only wire Tauri focus listener in the actual desktop app
    if (typeof globalThis.window !== 'undefined' && (globalThis.window as any).__TAURI_INTERNALS__) {
      import('@tauri-apps/api/window').then(({ getCurrentWindow }) => {
        getCurrentWindow().onFocusChanged(({ payload: focused }) => {
          if (focused) {
            queryClient.invalidateQueries({ queryKey: ['authStatus'] })
            queryClient.invalidateQueries({ queryKey: ['health'] })
          }
        }).then(unlisten => { cleanup = unlisten })
      })
    }
    return () => cleanup?.()
  }, [queryClient])

  useEffect(() => {
    if (!localStorage.getItem('pma_setup_complete') && location.pathname !== '/setup') {
      navigate('/setup', { replace: true })
    }
  }, [navigate, location.pathname])

  return (
    <div className="flex min-h-screen w-full">
      {/* ── Side Navigation ───────────────────────────────── */}
      <aside className="glass flex flex-col w-20 hover:w-56 transition-all duration-300 group border-r border-primary/10 fixed h-full z-50">
        {/* Logo */}
        <div className="flex items-center gap-3 px-5 py-6">
          <Brain className="w-8 h-8 text-primary shrink-0" />
          <span className="text-lg font-bold text-primary-light opacity-0 group-hover:opacity-100 transition-opacity duration-300 whitespace-nowrap">
            PMA
          </span>
        </div>

        {/* Nav Items */}
        <nav className="flex flex-col gap-1 px-3 mt-4 flex-1">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-3 rounded-xl transition-all duration-200 ${isActive
                  ? 'bg-white/80 text-primary shadow-[inset_2px_2px_4px_rgba(149,159,147,0.1),inset_-2px_-2px_4px_rgba(255,255,255,0.8),2px_2px_5px_rgba(149,159,147,0.2)]'
                  : 'text-text-secondary hover:bg-black/5 hover:text-text-primary'
                }`
              }
            >
              <Icon className="w-5 h-5 shrink-0" />
              <span className="opacity-0 group-hover:opacity-100 transition-opacity duration-300 whitespace-nowrap text-sm font-medium">
                {label}
              </span>
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-primary/10 flex flex-col gap-1">
          <span className="text-xs text-text-secondary opacity-0 group-hover:opacity-100 transition-opacity duration-300 whitespace-nowrap">
            v{appConfig?.app_version ?? health?.version ?? '—'}
            {appConfig?.gemini_model ? (
              <span className="block text-[10px] opacity-70 truncate max-w-[12rem]" title={appConfig.gemini_model}>
                {appConfig.gemini_model}
              </span>
            ) : null}
          </span>
          {/* Weekly Cost Roll-up */}
          <div className="text-[10px] text-text-secondary font-medium opacity-0 group-hover:opacity-100 transition-opacity duration-300 mt-2 whitespace-nowrap overflow-hidden">
            This week: ${weeklyCost.toFixed(3)}
          </div>
        </div>
      </aside>

      {/* ── Main Content ──────────────────────────────────── */}
      <main className="flex-1 ml-20 h-screen overflow-hidden flex flex-col">

        {/* Split-Brain sync banner — only visible while the boot-time back-fill is running */}
        {syncStatus === 'syncing' && (
          <div className="flex items-center gap-3 px-5 py-2.5 bg-warning/10 border-b border-warning/20 text-warning text-sm animate-fade-in-up">
            <RefreshCw className="w-4 h-4 shrink-0 animate-spin" />
            <span>
              <strong>Rebuilding vector index</strong> — PMA is migrating your embeddings into the local cache.
              Semantic search will be available once this completes.
            </span>
          </div>
        )}
        {syncStatus === 'error' && (
          <div className="flex items-center gap-3 px-5 py-2.5 bg-error/10 border-b border-error/20 text-error text-sm">
            <span>⚠ Vector index sync failed — check backend logs. Semantic search may return incomplete results.</span>
          </div>
        )}

        <Outlet />
      </main>
    </div>
  )
}
