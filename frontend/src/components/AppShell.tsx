import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { useApi } from '../useApi'
import { getAppConfig, getHealth, getIndexStatus, getProviderSettings, type IndexStatus } from '../api'
import { useCallback, useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSessionProvider } from '../context/SessionProviderContext'
import { CACHE_KEYS } from '../cacheKeys'
import { Grain, ThemeToggle, WorkProvider } from './ui'

/**
 * The room: a 72px numbered rail, the pane, a 28px status bar, and one grain
 * over all of it.
 *
 * The rail is fixed at 72px, so there is no collapsed state to remember and
 * every label is always on screen. The current pane takes a 2px lamp bar and
 * a lamp number; NavLink supplies `aria-current="page"`.
 *
 * AppShell.test.tsx locates each item by its label text. Caps are CSS, so the
 * text stays "Library". Ask leads: it is the verb the product exists for. Its
 * path stays /search so bookmarks, the setup redirect and e2e do not move.
 */
const navItems = [
  { to: '/search', label: 'Ask' },
  { to: '/library', label: 'Library' },
  { to: '/explorer', label: 'Explorer' },
  { to: '/insights', label: 'Insights' },
  { to: '/settings', label: 'Settings' },
] as const

export function AppShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { weeklyCost } = useSessionProvider()

  const isSyncing =
    (queryClient.getQueryData<{ split_brain_sync_status?: string }>(['health'])
      ?.split_brain_sync_status) === 'syncing'

  // Poll faster while a sync is in progress so the banner dismisses quickly
  const { data: health } = useApi(getHealth, {
    cacheKey: CACHE_KEYS.health,
    refetchInterval: isSyncing ? 5_000 : 60_000,
  })
  const { data: appConfig } = useApi(getAppConfig, {
    cacheKey: CACHE_KEYS.appConfig,
    refetchInterval: 60_000
  })

  // Onboarding can store a cloud API key and finish without ever collecting
  // consent, after which every query dies in the dispatch gate with the only
  // remedy on a page that is not in this nav. Server-computed so it cannot
  // disagree with the gate.
  const { data: routingSettings } = useApi(getProviderSettings, {
    cacheKey: CACHE_KEYS.providerSettings,
    refetchInterval: 60_000,
  })
  const consentRequired = routingSettings?.consent_required === true

  // The grain's busy signal, for the whole window. Ask reports searching,
  // writing and printing through useWorking. Indexing is read here as well,
  // because it runs on after you leave Library; polled faster while it runs so
  // the grain freezes soon after it ends. The call subscribes the shell to the
  // same cache entry `indexing` reads.
  const [working, setWorking] = useState(0)
  const reportWork = useCallback((delta: number) => setWorking((n) => n + delta), [])
  const indexState = queryClient.getQueryData<IndexStatus>([CACHE_KEYS.indexStatus])?.status
  const indexing = indexState === 'running' || indexState === 'cancelling'
  useApi(getIndexStatus, {
    cacheKey: CACHE_KEYS.indexStatus,
    refetchInterval: indexing ? 10_000 : 60_000,
  })

  const syncStatus = health?.split_brain_sync_status

  // Only genuine faults surface. 'disabled' and 'unknown' are not faults, so a
  // default install (ocr_enabled=False) shows nothing at all here.
  const downSubsystems = Object.entries(health?.subsystems ?? {})
    .filter(([, info]) => info.state === 'down')
    .map(([name]) => name)

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

  /**
   * One status region, severity-ordered.
   *
   * Three stacked full-width banners used to push content down and compete for
   * the same attention. At most one shows now: a failed sync outranks a missing
   * consent, which outranks a sync in progress.
   */
  let notice: { tone: string; border: string; body: React.ReactNode; action: React.ReactNode } | null = null
  if (syncStatus === 'error') {
    notice = {
      tone: 'text-error',
      border: 'border-b-error/40',
      body: (
        <span className="text-text-primary">
          <strong className="font-medium">Vector index sync failed</strong>
          <span className="text-text-secondary"> — semantic search may return incomplete results.</span>
        </span>
      ),
      action: (
        <NavLink to="/settings/diagnostics" className="tap-24 text-primary underline underline-offset-4 font-medium shrink-0">
          Diagnostics
        </NavLink>
      ),
    }
  } else if (consentRequired) {
    notice = {
      tone: 'text-warning',
      border: 'border-b-warning/40',
      body: (
        <span className="text-text-primary">
          <strong className="font-medium">Cloud provider needs your consent</strong>
          <span className="text-text-secondary"> — answers will fail until you review it.</span>
        </span>
      ),
      action: (
        <NavLink
          to="/settings/providers#cloud-consent"
          className="tap-24 text-primary underline underline-offset-4 font-medium shrink-0"
        >
          Review now
        </NavLink>
      ),
    }
  } else if (syncStatus === 'syncing') {
    notice = {
      // Working, not a fault and not the live thing: ink, never the lamp.
      tone: 'text-text-secondary',
      border: 'border-b-rule',
      body: (
        <span className="text-text-primary">
          <strong className="font-medium">Rebuilding vector index</strong>
          <span className="text-text-secondary"> — semantic search returns once this completes.</span>
        </span>
      ),
      action: null,
    }
  }

  return (
    <WorkProvider value={reportWork}>
    <div className="flex-1 min-w-0 h-screen flex flex-col bg-background">
      <div className="flex-1 min-h-0 flex">

        {/* ── The rail ─────────────────────────────────────────────── */}
        <nav aria-label="Main" className="w-[72px] shrink-0 flex flex-col pt-1.5 pb-3 border-r border-rule">
          {navItems.map(({ to, label }, i) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                'relative h-[58px] flex flex-col items-center justify-center gap-1 transition-colors duration-120 ' +
                (isActive ? 'text-text-primary' : 'text-text-tertiary hover:text-text-primary')
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && <span aria-hidden className="absolute left-0 top-[15px] bottom-[15px] w-0.5 bg-plate" />}
                  <span aria-hidden className={`font-mono text-[10px] tracking-[.1em] ${isActive ? 'text-accent' : ''}`}>
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <span className="font-bold [font-stretch:70%] text-xs tracking-[.05em] uppercase">{label}</span>
                </>
              )}
            </NavLink>
          ))}
          <div className="mt-auto flex justify-center">
            <ThemeToggle />
          </div>
        </nav>

        {/* ── The pane ─────────────────────────────────────────────── */}
        <main className="flex-grow min-w-0 flex flex-col">
          {notice && (
            <div className={`flex items-center gap-3 px-5 py-2 bg-surface border-b ${notice.border} text-[13px]`}>
              {/* State is a square beside a word, never an icon or a dot. */}
              <span aria-hidden className={`w-2 h-2 shrink-0 bg-current ${notice.tone}`} />
              {notice.body}
              {notice.action && <span className="ml-auto">{notice.action}</span>}
            </div>
          )}

          <div className="flex-grow min-w-0 overflow-hidden flex flex-col">
            <Outlet />
          </div>
        </main>
      </div>

      {/* ── Status bar ─────────────────────────────────────────────── */}
      <footer className="h-7 shrink-0 flex items-center gap-[18px] px-3.5 border-t border-rule text-text-tertiary font-mono text-[10px] tracking-[.1em] uppercase whitespace-nowrap overflow-hidden">
        {/* Degraded optional subsystems. A fault the user cannot see is the
            whole problem this reports, so it is never hover-gated. */}
        {downSubsystems.length > 0 && (
          <NavLink
            to="/settings/diagnostics"
            data-testid="subsystem-warning"
            title={`Not running: ${downSubsystems.join(', ')}. Open Diagnostics for the reason.`}
            className="flex items-center gap-2 text-warning hover:text-text-primary"
          >
            <span aria-hidden className="w-2 h-2 shrink-0 bg-current" />
            <span className="truncate">{downSubsystems.join(', ')} off</span>
          </NavLink>
        )}
        {/* Dollars to four places: a local answer is $0.0000. */}
        <span className="ml-auto">
          This week ${weeklyCost.toFixed(4)} · v{appConfig?.app_version ?? health?.version ?? '—'}
        </span>
      </footer>
      <Grain busy={working > 0 || indexing} />
    </div>
    </WorkProvider>
  )
}
