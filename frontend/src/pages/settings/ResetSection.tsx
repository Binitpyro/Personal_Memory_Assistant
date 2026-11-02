/**
 * Extracted from SettingsPage.tsx, which had grown to 1341 lines holding ten
 * unrelated section components in one unbroken scroll. Behaviour is unchanged;
 * only the file boundary moved.
 */
import { Button } from '../../components/ui'

export function ResetSection({ onRestartOnboarding, onFullReset }: Readonly<{ onRestartOnboarding: () => void; onFullReset: () => void }>) {
  return (
    <div className="glass p-6 rounded-2xl border border-error/10 bg-error/5">
      <div className="flex items-start gap-4 mb-6">
        <div>
          <h2 className="font-bold [font-stretch:80%] text-lg text-text-primary m-0">Showcase & Reset</h2>
          <p className="text-sm text-text-secondary mt-1">
            Use these options to prepare the app for a demonstration or fresh start.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap gap-4">
        <Button onClick={onRestartOnboarding} icon={<span aria-hidden>↻</span>}>
          Restart Onboarding
        </Button>
        <Button variant="danger" onClick={onFullReset}>
          Full Application Reset
        </Button>
      </div>
    </div>
  )
}

// ── Main Page Component ──────────────────────────────────────────────
