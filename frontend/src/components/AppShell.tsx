import { Outlet, NavLink } from 'react-router-dom'
import { BookOpen, Search, FolderTree, BarChart3, Brain } from 'lucide-react'
import { useApi } from '../useApi'
import { getHealth } from '../api'

const navItems = [
  { to: '/library', label: 'Library', icon: BookOpen },
  { to: '/search', label: 'Search', icon: Search },
  { to: '/explorer', label: 'Explorer', icon: FolderTree },
  { to: '/insights', label: 'Insights', icon: BarChart3 },
] as const

export function AppShell() {
  const { data: health } = useApi(getHealth, { cacheKey: 'health', refetchInterval: 60_000 })

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
                `flex items-center gap-3 px-3 py-3 rounded-xl transition-all duration-200 ${
                  isActive
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
        <div className="px-5 py-4 border-t border-primary/10">
          <span className="text-xs text-text-secondary opacity-0 group-hover:opacity-100 transition-opacity duration-300 whitespace-nowrap">
            v{health?.version ?? '0.0.41'}
          </span>
        </div>
      </aside>

      {/* ── Main Content ──────────────────────────────────── */}
      <main className="flex-1 ml-20 h-screen overflow-hidden flex flex-col">
        <Outlet />
      </main>
    </div>
  )
}
