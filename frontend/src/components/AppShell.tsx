import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import {
  AlertTriangle, RefreshCw,
  BookOpen, Search, Folder, BarChart3, Settings,
  PanelLeftClose, PanelLeftOpen,
} from 'lucide-react'
import { useApi } from '../useApi'
import { getAppConfig, getHealth, getProviderSettings } from '../api'
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSessionProvider } from '../context/SessionProviderContext'
import { CACHE_KEYS } from '../cacheKeys'
import { ThemeToggle } from './ui'

/**
 * The catalogue index.
 *
 * Each route is a drawer front: an icon and the route's own name. The rail
 * collapses to icons only, and nothing is hover-gated — in both states the
 * label is reachable, because collapsing hides it visually (`sr-only`) rather
 * than unmounting it.
 *
 * The roman-numeral marks (`I · LIB` … `V · SET`) were removed: at 10px they
 * were unreadable, and `SRCH`/`EXPL`/`INS` only abbreviated the word sitting
 * directly beneath them. The icon carries the same "which drawer" job legibly.
 *
 * The `label` values must stay exactly as the route names: AppShell.test.tsx
 * locates each one by text.
 */
const navItems = [
  { to: '/library', icon: BookOpen, label: 'Library' },
  { to: '/search', icon: Search, label: 'Search' },
  { to: '/explorer', icon: Folder, label: 'Explorer' },
  { to: '/insights', icon: BarChart3, label: 'Insights' },
  { to: '/settings', icon: Settings, label: 'Settings' },
] as const

const NAV_COLLAPSED_KEY = 'pma_nav_collapsed'

export function AppShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { weeklyCost } = useSessionProvider()

  // Read once on mount rather than in a useEffect, so the rail paints at its
  // stored width on the first frame instead of snapping open then shut.
  const [navCollapsed, setNavCollapsed] = useState(() => {
    try { return localStorage.getItem(NAV_COLLAPSED_KEY) === '1' } catch { return false }
  })
  const toggleNav = () => {
    setNavCollapsed((v) => {
      const next = !v
      try { localStorage.setItem(NAV_COLLAPSED_KEY, next ? '1' : '0') } catch { /* private mode */ }
      return next
    })
  }

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
      tone: 'text-primary',
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
    // The case: a hard outer rail, with everything set inside it.
    <div className="flex-1 min-w-0 h-screen box-border bg-raised p-2">
      <div className="h-full border border-edge flex overflow-hidden">

        {/* ── Catalogue index ──────────────────────────────────────── */}
        <aside
          className={
            'shrink-0 bg-raised flex flex-col border-r border-black/40 transition-[width] duration-200 ' +
            (navCollapsed ? 'w-[68px]' : 'w-[216px]')
          }
        >
          <div className={'border-b border-black/40 ' + (navCollapsed ? 'px-2 py-3' : 'px-4 pt-4 pb-3')}>
            <div className="flex items-start gap-2">
              {!navCollapsed && (
                <div className="min-w-0 flex-grow">
                  <div className="font-mono text-xs tracking-widest uppercase text-text-tertiary">
                    Personal Memory Assistant
                  </div>
                  <div className="font-serif text-lg font-medium mt-0.5">The Cabinet</div>
                </div>
              )}
              {/* 28x28 clears the 24x24 target minimum. */}
              <button
                type="button"
                onClick={toggleNav}
                aria-expanded={!navCollapsed}
                aria-label={navCollapsed ? 'Expand navigation' : 'Collapse navigation'}
                title={navCollapsed ? 'Expand navigation' : 'Collapse navigation'}
                className={
                  'shrink-0 grid place-items-center w-7 h-7 rounded-md border border-edge bg-surface ' +
                  'text-text-secondary hover:text-text-primary hover:bg-raised transition-colors ' +
                  (navCollapsed ? 'mx-auto' : '')
                }
              >
                {navCollapsed
                  ? <PanelLeftOpen className="w-4 h-4" aria-hidden />
                  : <PanelLeftClose className="w-4 h-4" aria-hidden />}
              </button>
            </div>
          </div>

          <nav className="flex flex-col">
            {navItems.map(({ to, icon: Icon, label }) => (
              <NavLink key={to} to={to} className="block" title={navCollapsed ? label : undefined}>
                {({ isActive }) => (
                  <div
                    className={
                      (navCollapsed ? 'px-2 py-3 ' : 'px-3 py-2.5 ') +
                      'border-t border-t-white/[0.06] border-b border-b-black/40 transition-colors duration-150 ' +
                      (isActive
                        ? 'bg-surface shadow-[inset_3px_0_0_var(--color-plate),5px_0_12px_rgba(0,0,0,.45)]'
                        : 'bg-raised hover:bg-surface')
                    }
                  >
                    <div className={`flex items-center gap-3 ${navCollapsed ? 'justify-center' : ''}`}>
                      <Icon
                        aria-hidden
                        className={`w-[18px] h-[18px] shrink-0 ${isActive ? 'text-primary' : 'text-text-tertiary'}`}
                      />
                      {/* Collapsing hides the label, it does not unmount it: the
                          accessible name has to survive, and AppShell.test.tsx
                          finds each route by this text. */}
                      <div className={navCollapsed ? 'sr-only' : 'flex-grow min-w-0'}>
                        <div className="font-serif text-base leading-tight truncate">{label}</div>
                      </div>
                    </div>
                  </div>
                )}
              </NavLink>
            ))}
          </nav>

          {/* Colophon */}
          <div className={'mt-auto border-t border-black/40 flex flex-col gap-2 ' + (navCollapsed ? 'px-2 py-3 items-center' : 'px-4 py-3')}>
            {/* Degraded optional subsystems. A fault the user cannot see is the
                whole problem this reports, so it is never hover-gated. */}
            {downSubsystems.length > 0 && (
              <NavLink
                to="/settings/diagnostics"
                data-testid="subsystem-warning"
                title={`Not running: ${downSubsystems.join(', ')}. Open Diagnostics for the reason.`}
                className="flex items-center gap-2 text-warning text-xs hover:opacity-80"
              >
                <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                <span className="truncate">{downSubsystems.join(', ')} off</span>
              </NavLink>
            )}
            {!navCollapsed && (
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs text-text-tertiary">this week</span>
                <span className="font-mono text-xs text-text-secondary">${weeklyCost.toFixed(3)}</span>
              </div>
            )}
            <div className={`flex items-center gap-2 ${navCollapsed ? 'flex-col' : 'justify-between'}`}>
              {!navCollapsed && (
                <span className="font-mono text-xs text-text-tertiary">
                  v{appConfig?.app_version ?? health?.version ?? '—'}
                </span>
              )}
              <ThemeToggle />
            </div>
          </div>
        </aside>

        {/* ── Main ─────────────────────────────────────────────────── */}
        <main className="flex-grow min-w-0 flex flex-col bg-raised">
          {notice && (
            <div className={`flex items-center gap-3 px-5 py-2 bg-surface border-b ${notice.border} text-[13px]`}>
              {syncStatus === 'syncing' ? (
                <RefreshCw className={`w-3.5 h-3.5 shrink-0 animate-spin ${notice.tone}`} />
              ) : (
                <AlertTriangle className={`w-3.5 h-3.5 shrink-0 ${notice.tone}`} />
              )}
              {notice.body}
              {notice.action && <span className="ml-auto">{notice.action}</span>}
            </div>
          )}

          {/* The well: content is cut into the case, not floated on it. */}
          <div className="well flex-grow min-w-0 m-2.5 overflow-hidden flex flex-col">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
