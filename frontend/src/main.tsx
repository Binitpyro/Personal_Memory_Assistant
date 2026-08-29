import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './fonts'
import './index.css'
import { AppShell } from './components/AppShell'
import { initTauriConnection } from './api';
import { initTheme } from './theme'
import { initFonts } from './fonts'

import { QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { queryClient } from './queryClient'
import { SessionProvider } from './context/SessionProviderContext'

// Lazy-load pages so the initial bundle only contains the shell + current route
const LibraryPage = lazy(() => import('./pages/LibraryPage').then(m => ({ default: m.LibraryPage })))
const SearchPage = lazy(() => import('./pages/SearchPage').then(m => ({ default: m.SearchPage })))
const ExplorerPage = lazy(() => import('./pages/ExplorerPage').then(m => ({ default: m.ExplorerPage })))
const InsightsPage = lazy(() => import('./pages/InsightsPage').then(m => ({ default: m.InsightsPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then(m => ({ default: m.SettingsPage })))
const SetupPage = lazy(() => import('./pages/SetupPage').then(m => ({ default: m.SetupPage })))
const ProvidersPage = lazy(() => import('./pages/ProvidersPage').then(m => ({ default: m.ProvidersPage })))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage').then(m => ({ default: m.NotFoundPage })))
const DiagnosticsPage = lazy(() => import('./pages/DiagnosticsPage').then(m => ({ default: m.DiagnosticsPage })))
const SettingsLayout = lazy(() => import('./pages/settings/SettingsLayout').then(m => ({ default: m.SettingsLayout })))

function PageLoader() {
  return (
    <div className="flex items-center justify-center min-h-[50vh]">
      <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
    </div>
  )
}

// Before render, so a stored choice is on the root element for the first paint.
initTheme();
// Gates serif text on document.fonts.ready so Newsreader does not pop in.
initFonts();

await initTauriConnection();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SessionProvider>
        <BrowserRouter>
          <Suspense fallback={<PageLoader />}>
            <Routes>
              <Route path="/setup" element={<SetupPage />} />
              <Route element={<AppShell />}>
                <Route path="/" element={<Navigate to="/library" replace />} />
                <Route path="/library" element={<LibraryPage />} />
                <Route path="/search" element={<SearchPage />} />
                <Route path="/explorer" element={<ExplorerPage />} />
                <Route path="/insights" element={<InsightsPage />} />
                {/* Nested so Providers and Diagnostics keep the Settings rail
                    instead of navigating away from it. The paths are unchanged,
                    so every existing deep link still resolves. */}
                <Route path="/settings" element={<SettingsLayout />}>
                  <Route index element={<SettingsPage />} />
                  <Route path="providers" element={<ProvidersPage />} />
                  <Route path="diagnostics" element={<DiagnosticsPage />} />
                </Route>
                {/* Inside AppShell so an unknown path still renders the nav. */}
                <Route path="*" element={<NotFoundPage />} />
              </Route>
            </Routes>
          </Suspense>
        </BrowserRouter>
        {/* Was hardcoded `dark` on a light app. `system` tracks the OS, which is
            also what the CSS does when the user has not chosen a theme; the
            explicit toggle wires through in Phase 5. */}
        <Toaster theme="system" position="bottom-right" richColors closeButton />
      </SessionProvider>
    </QueryClientProvider>
  </StrictMode>,
)

