/**
 * Extracted from SettingsPage.tsx, which had grown to 1341 lines holding ten
 * unrelated section components in one unbroken scroll. Behaviour is unchanged;
 * only the file boundary moved.
 */
import { RefreshCcw, Trash2 } from 'lucide-react'

export function ResetSection({ onRestartOnboarding, onFullReset }: Readonly<{ onRestartOnboarding: () => void; onFullReset: () => void }>) {
  return (
    <div className="glass p-6 rounded-2xl border border-error/10 bg-error/5">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-error/10 rounded-xl">
          <RefreshCcw className="w-6 h-6 text-error" />
        </div>
        <div>
          <h2 className="font-serif text-lg font-medium text-text-primary">Showcase & Reset</h2>
          <p className="text-sm text-text-secondary mt-1">
            Use these options to prepare the app for a demonstration or fresh start.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap gap-4">
        <button
          onClick={onRestartOnboarding}
          className="glass-button !text-text-primary hover:bg-raised !py-2 !px-4 gap-2 border border-rule"
        >
          <RefreshCcw className="w-4 h-4" />
          Restart Onboarding
        </button>
        <button
          onClick={onFullReset}
          className="glass-button !text-error hover:bg-error/10 !py-2 !px-4 gap-2 border border-error/20"
        >
          <Trash2 className="w-4 h-4" />
          Full Application Reset
        </button>
      </div>
    </div>
  )
}

// ── Main Page Component ──────────────────────────────────────────────
