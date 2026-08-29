/**
 * Extracted from SettingsPage.tsx, which had grown to 1341 lines holding ten
 * unrelated section components in one unbroken scroll. Behaviour is unchanged;
 * only the file boundary moved.
 */
import { CheckCircle2, HardDrive, Trash2, AlertTriangle, DatabaseZap } from 'lucide-react'
import { type DriveInfo } from '../../api'

export function SplitBrainSection({
  driveInfo,
  onPurge,
  purging
}: Readonly<{
  driveInfo?: DriveInfo;
  onPurge: () => void;
  purging: boolean;
}>) {
  if (!driveInfo) return null

  const isAtRisk = driveInfo.is_portable_fs && driveInfo.lancedb_mode !== 'split_brain'
  const cardClasses = `glass p-6 rounded-2xl border ${isAtRisk ? 'border-warning/30 bg-warning/5' : 'border-primary/10'}`
  const iconBgClasses = `p-3 rounded-xl ${isAtRisk ? 'bg-warning/10' : 'bg-primary/10'}`
  const iconClasses = `w-6 h-6 ${isAtRisk ? 'text-warning' : 'text-primary'}`

  return (
    <div className={cardClasses}>
      <div className="flex items-start gap-4 mb-5">
        <div className={iconBgClasses}>
          <DatabaseZap className={iconClasses} />
        </div>
        <div>
          <h2 className="font-serif text-lg font-medium text-text-primary">Vector Cache &amp; Portability</h2>
          <p className="text-sm text-text-secondary mt-1">
            Manage the local LanceDB host cache used in Split-Brain mode.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-5 text-sm">
        <div className="p-3 rounded-xl bg-raised border border-rule">
          <span className="text-text-secondary block mb-1">Drive</span>
          <span className="font-semibold text-text-primary flex items-center gap-1.5">
            <HardDrive className="w-3.5 h-3.5 text-primary" />
            {driveInfo.drive || '–'}
          </span>
        </div>
        <div className="p-3 rounded-xl bg-raised border border-rule">
          <span className="text-text-secondary block mb-1">Filesystem</span>
          <span className={`font-semibold ${driveInfo.is_portable_fs ? 'text-warning' : 'text-success'}`}>
            {driveInfo.fs_type}
          </span>
        </div>
        <div className="p-3 rounded-xl bg-raised border border-rule">
          <span className="text-text-secondary block mb-1">LanceDB Mode</span>
          <span className={`font-semibold ${driveInfo.lancedb_mode === 'split_brain' ? 'text-success' : 'text-text-primary'}`}>
            {driveInfo.lancedb_mode === 'split_brain' ? 'Split-Brain ✓' : 'Portable'}
          </span>
        </div>
        <div className="p-3 rounded-xl bg-raised border border-rule">
          <span className="text-text-secondary block mb-1">Index Safety</span>
          {isAtRisk ? (
            <span className="font-semibold text-warning flex items-center gap-1">
              <AlertTriangle className="w-3.5 h-3.5" /> At Risk
            </span>
          ) : (
            <span className="font-semibold text-success flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5" /> Safe
            </span>
          )}
        </div>
      </div>

      {driveInfo.lancedb_mode === 'split_brain' && (
        <div className="flex items-center gap-3">
          <button
            onClick={onPurge}
            disabled={purging}
            className="glass-button text-warning hover:bg-warning/10 !py-2 !px-4 gap-2 border border-warning/20 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Trash2 className="w-4 h-4" />
            {purging ? 'Purging…' : 'Purge Host Cache'}
          </button>
          <p className="text-xs text-text-secondary">
            Deletes the local vector index. PMA rebuilds from the portable SQLite database on next restart.
          </p>
        </div>
      )}
    </div>
  )
}
