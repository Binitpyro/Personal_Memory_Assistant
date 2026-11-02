/**
 * Extracted from SettingsPage.tsx, which had grown to 1341 lines holding ten
 * unrelated section components in one unbroken scroll. Behaviour is unchanged;
 * only the file boundary moved.
 */
import { useNavigate } from 'react-router-dom'
import { Button } from '../../components/ui'

export function DiagnosticsSection() {
  const navigate = useNavigate()
  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <div>
            <h2 className="font-bold [font-stretch:80%] text-lg text-text-primary m-0">Diagnostics</h2>
            <p className="text-sm text-text-secondary mt-1">
              Subsystem health, query latency, the OCR engine in use, and database maintenance.
            </p>
          </div>
        </div>
        <Button variant="secondary" size="sm" className="shrink-0" onClick={() => navigate('/settings/diagnostics')}>
          Open <span aria-hidden>›</span>
        </Button>
      </div>
    </div>
  )
}
