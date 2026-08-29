/**
 * Extracted from SettingsPage.tsx, which had grown to 1341 lines holding ten
 * unrelated section components in one unbroken scroll. Behaviour is unchanged;
 * only the file boundary moved.
 */
import { useNavigate } from 'react-router-dom'
import { Activity, ChevronRight } from 'lucide-react'

export function DiagnosticsSection() {
  const navigate = useNavigate()
  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <div className="p-3 bg-primary/10 rounded-xl">
            <Activity className="w-6 h-6 text-primary" />
          </div>
          <div>
            <h2 className="font-serif text-lg font-medium text-text-primary">Diagnostics</h2>
            <p className="text-sm text-text-secondary mt-1">
              Subsystem health, query latency, the OCR engine in use, and database maintenance.
            </p>
          </div>
        </div>
        <button
          onClick={() => navigate('/settings/diagnostics')}
          className="glass-button !bg-primary/10 !text-primary px-4 py-2 rounded-lg text-sm font-semibold shrink-0 flex items-center gap-1"
        >
          Open <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
