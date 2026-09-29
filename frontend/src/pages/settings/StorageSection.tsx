/**
 * Extracted from SettingsPage.tsx, which had grown to 1341 lines holding ten
 * unrelated section components in one unbroken scroll. Behaviour is unchanged;
 * only the file boundary moved.
 */
import { type SystemInfo } from '../../api'

export function StorageSection({ sysInfo }: Readonly<{ sysInfo?: SystemInfo }>) {
  const getProgressColor = (pct: number) => {
    if (pct > 90) return 'bg-error'
    if (pct > 75) return 'bg-warning'
    return 'bg-primary'
  }

  if (!sysInfo?.volumes || sysInfo.volumes.length === 0) return null

  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div>
          <h2 className="font-bold [font-stretch:80%] text-lg text-text-primary m-0">Storage</h2>
          <p className="text-sm text-text-secondary mt-1">Disk usage on indexed volumes.</p>
        </div>
      </div>
      <div className="flex flex-col gap-4">
        {sysInfo.volumes.map(v => {
          const pct = Math.round((v.used_gb / v.total_gb) * 100)
          return (
            <div key={v.letter}>
              <div className="flex justify-between text-xs font-medium text-text-secondary mb-1.5">
                <span className="font-bold text-text-primary">{v.letter}:</span>
                <span>{v.used_gb.toFixed(1)} GB used of {v.total_gb.toFixed(1)} GB</span>
              </div>
              <div className="h-2 bg-raised border border-rule overflow-hidden">
                <div
                  className={`h-full transition-all duration-700 ${getProgressColor(pct)}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="text-right text-xs text-text-secondary mt-1">{pct}% used · {v.free_gb.toFixed(1)} GB free</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
