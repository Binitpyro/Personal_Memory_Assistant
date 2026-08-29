import { NavLink, Outlet } from 'react-router-dom'

/**
 * Settings sub-navigation.
 *
 * `/settings/providers` and `/settings/diagnostics` were sibling TOP-LEVEL
 * routes, so reaching either one left Settings entirely — there was no way back
 * except the sidebar, and no indication the three were related at all. They are
 * nested under this layout now and share a rail.
 *
 * The 1239-line SettingsPage is deliberately NOT split here. That is a pure
 * refactor with no user-visible effect, and it would put 11 passing tests at
 * risk for no behavioural gain; this delivers the IA change on its own.
 */
const tabs = [
  { to: '/settings', end: true, mark: 'A', label: 'General' },
  { to: '/settings/providers', end: false, mark: 'B', label: 'Providers' },
  { to: '/settings/diagnostics', end: false, mark: 'C', label: 'Diagnostics' },
] as const

export function SettingsLayout() {
  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <nav
        aria-label="Settings sections"
        className="shrink-0 flex items-stretch gap-0 border-b border-rule bg-surface"
      >
        {tabs.map(({ to, end, mark, label }) => (
          <NavLink key={to} to={to} end={end} className="block">
            {({ isActive }) => (
              <div
                className={
                  'px-5 py-2.5 border-r border-rule transition-colors duration-150 ' +
                  (isActive
                    ? 'bg-raised shadow-[inset_0_2px_0_var(--color-plate)]'
                    : 'hover:bg-raised')
                }
              >
                <div
                  className={`font-mono text-[10px] tracking-[0.16em] uppercase ${
                    isActive ? 'text-primary' : 'text-text-tertiary'
                  }`}
                >
                  {mark}
                </div>
                <div className="font-serif text-[15px] leading-tight">{label}</div>
              </div>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="flex-1 min-h-0 flex flex-col">
        <Outlet />
      </div>
    </div>
  )
}
