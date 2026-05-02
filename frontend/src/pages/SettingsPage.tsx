import { useState, useEffect } from 'react'
import { Settings, Shield, Key, CheckCircle2, AlertCircle, LogOut, Cpu, HardDrive, RefreshCcw, Trash2, AlertTriangle, DatabaseZap } from 'lucide-react'
import { useApi, invalidateCache } from '../useApi'
import {
  getAuthStatus,
  disconnectAuth,
  getLocalModels,
  getSystemInfo,
  getLLMPreferences,
  setLLMPreferences,
  clearIndex,
  launchGoogleAuth,
  getDriveInfo,
  purgeHostCache,
  type LLMPreferences,
  type AuthStatus,
  type LocalModelDetection,
  type SystemInfo,
  type DriveInfo
} from '../api'

// ── Sub-components for lower cognitive complexity ───────────────────

function AuthSection({
  authStatus,
  onConnect,
  onDisconnect
}: {
  authStatus?: AuthStatus;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  const isConnected = !!authStatus?.connected
  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-4">
          <div className="p-3 bg-primary/10 rounded-xl">
            <Shield className="w-6 h-6 text-primary" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-text-primary">Google Gemini Account</h2>
            <p className="text-sm text-text-secondary mt-1 max-w-lg">
              Connect your Google Account to use the Gemini AI natively without needing an API key.
              Your token is stored safely on your local machine.
            </p>

            <div className="mt-4 flex items-center gap-2">
              <span className="text-sm font-medium text-text-primary">Status:</span>
              {isConnected ? (
                <span className="inline-flex items-center justify-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-success/15 text-success">
                  <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse"></span>
                  {authStatus?.method === 'env' ? 'Connected (.env)' : 'Connected'}
                </span>
              ) : (
                <span className="inline-flex items-center justify-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-text-secondary/15 text-text-secondary">
                  <span className="w-1.5 h-1.5 rounded-full bg-text-secondary"></span>
                  Not Connected
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="shrink-0 flex flex-col gap-2">
          {isConnected ? (
            authStatus?.method !== 'env' && (
              <button
                onClick={onDisconnect}
                className="glass-button text-error hover:bg-error/10 !py-2 !px-4 gap-2 border border-error/20"
              >
                <LogOut className="w-4 h-4" />
                Disconnect
              </button>
            )
          ) : (
            <button
              onClick={onConnect}
              className="glass-button !bg-primary !text-white hover:!bg-primary-h !py-2 !px-4 gap-2"
            >
              <Key className="w-4 h-4" />
              Connect with Google
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function LocalModelsSection({ localModels }: { localModels?: LocalModelDetection }) {
  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-primary/10 rounded-xl">
          <Cpu className="w-6 h-6 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-text-primary">Local LLM Auto-Detection</h2>
          <p className="text-sm text-text-secondary mt-1 max-w-lg">
            Offline models running on your machine are detected automatically.
            The backend will cascade to these if Google Gemini is unavailable or not configured.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Ollama Box */}
        <div className="p-4 rounded-xl border border-primary/5 bg-white/50 backdrop-blur-md">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-text-primary flex items-center gap-2">
              <span className="text-xl">🦙</span> Ollama
            </h3>
            {localModels?.ollama.detected ? (
              <span className="inline-flex items-center justify-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-success/15 text-success">
                <span className="w-1.5 h-1.5 rounded-full bg-success"></span>
                Detected
              </span>
            ) : (
              <span className="inline-flex items-center justify-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-text-secondary/15 text-text-secondary">
                Offline
              </span>
            )}
          </div>
          {localModels?.ollama.detected ? (
            <div className="flex flex-wrap gap-2 mt-2">
              {localModels.ollama.models.map(m => (
                <span key={m} className="px-2 py-1 text-xs font-medium bg-primary/10 text-primary rounded-md border border-primary/20">
                  {m}
                </span>
              ))}
              {localModels.ollama.models.length === 0 && (
                <span className="text-sm text-text-secondary">Running, but no models installed.</span>
              )}
            </div>
          ) : (
            <p className="text-sm text-text-secondary">Ensure Ollama is running on localhost:11434.</p>
          )}
        </div>

        {/* LM Studio Box */}
        <div className="p-4 rounded-xl border border-primary/5 bg-white/50 backdrop-blur-md">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-text-primary flex items-center gap-2">
              <span className="text-xl">🖥️</span> LM Studio
            </h3>
            {localModels?.lm_studio.detected ? (
              <span className="inline-flex items-center justify-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-success/15 text-success">
                <span className="w-1.5 h-1.5 rounded-full bg-success"></span>
                Detected
              </span>
            ) : (
              <span className="inline-flex items-center justify-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-text-secondary/15 text-text-secondary">
                Offline
              </span>
            )}
          </div>
          {localModels?.lm_studio.detected ? (
            <div className="flex flex-wrap gap-2 mt-2">
              {localModels.lm_studio.models.map(m => (
                <span key={m} className="px-2 py-1 text-xs font-medium bg-primary/10 text-primary rounded-md border border-primary/20">
                  {m}
                </span>
              ))}
              {localModels.lm_studio.models.length === 0 && (
                <span className="text-sm text-text-secondary">Running, but no model loaded.</span>
              )}
            </div>
          ) : (
            <p className="text-sm text-text-secondary">Ensure LM Studio's Local Server is running on localhost:1234.</p>
          )}
        </div>
      </div>
    </div>
  )
}

function LLMPreferencesSection({
  localModels,
  onSave,
  saving
}: {
  localModels?: LocalModelDetection;
  onSave: (prefs: LLMPreferences) => void;
  saving: boolean;
}) {
  const { data: llmPrefs } = useApi(getLLMPreferences, { cacheKey: 'llm-prefs' })
  const [provider, setProvider] = useState<LLMPreferences['provider']>('auto')
  const [geminiModel, setGeminiModel] = useState('gemini-2.5-flash-lite')
  const [ollamaModel, setOllamaModel] = useState('')
  const [lmStudioModel, setLmStudioModel] = useState('')

  useEffect(() => {
    if (llmPrefs) {
      setProvider(llmPrefs.provider || 'auto')
      setGeminiModel(llmPrefs.gemini_model || 'gemini-2.5-flash-lite')
      setOllamaModel(llmPrefs.ollama_model || '')
      setLmStudioModel(llmPrefs.lm_studio_model || '')
    }
  }, [llmPrefs])

  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-primary/10 rounded-xl">
          <Cpu className="w-6 h-6 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-text-primary">Advanced Model Selection</h2>
          <p className="text-sm text-text-secondary mt-1">
            Choose your preferred provider and default model. PMA applies these preferences at runtime.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="text-sm text-text-secondary flex flex-col gap-1">
          Provider
          <select value={provider} onChange={(e) => setProvider(e.target.value as any)} className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-text-primary">
            <option value="auto">Auto (recommended)</option>
            <option value="gemini">Gemini</option>
            <option value="ollama">Ollama</option>
            <option value="lm_studio">LM Studio</option>
          </select>
        </label>

        <label className="text-sm text-text-secondary flex flex-col gap-1">
          Gemini model
          <input value={geminiModel} onChange={(e) => setGeminiModel(e.target.value)} className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-text-primary" placeholder="gemini-2.5-flash-lite" />
        </label>

        <label className="text-sm text-text-secondary flex flex-col gap-1">
          Ollama model
          <select value={ollamaModel} onChange={(e) => setOllamaModel(e.target.value)} className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-text-primary">
            <option value="">(auto)</option>
            {(localModels?.ollama.models || []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>

        <label className="text-sm text-text-secondary flex flex-col gap-1">
          LM Studio model
          <select value={lmStudioModel} onChange={(e) => setLmStudioModel(e.target.value)} className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-text-primary">
            <option value="">(auto)</option>
            {(localModels?.lm_studio.models || []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
      </div>

      <div className="mt-4">
        <button
          onClick={() => onSave({ provider, gemini_model: geminiModel, ollama_model: ollamaModel, lm_studio_model: lmStudioModel })}
          disabled={saving}
          className="glass-button !bg-primary !text-white hover:!bg-primary-h !py-2 !px-4"
        >
          {saving ? 'Saving...' : 'Save LLM Preferences'}
        </button>
      </div>
    </div>
  )
}

function StorageSection({ sysInfo }: { sysInfo?: SystemInfo }) {
  const getProgressColor = (pct: number) => {
    if (pct > 90) return 'bg-error'
    if (pct > 75) return 'bg-warning'
    return 'bg-primary'
  }

  if (!sysInfo?.volumes || sysInfo.volumes.length === 0) return null

  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-primary/10 rounded-xl">
          <HardDrive className="w-6 h-6 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-text-primary">Storage</h2>
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
              <div className="h-2 bg-white/10 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-700 ${getProgressColor(pct)}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="text-right text-[10px] text-text-secondary mt-1">{pct}% used · {v.free_gb.toFixed(1)} GB free</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function SplitBrainSection({
  driveInfo,
  onPurge,
  purging
}: {
  driveInfo?: DriveInfo;
  onPurge: () => void;
  purging: boolean;
}) {
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
          <h2 className="text-lg font-bold text-text-primary">Vector Cache &amp; Portability</h2>
          <p className="text-sm text-text-secondary mt-1">
            Manage the local LanceDB host cache used in Split-Brain mode.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-5 text-sm">
        <div className="p-3 rounded-xl bg-white/5 border border-white/10">
          <span className="text-text-secondary block mb-1">Drive</span>
          <span className="font-semibold text-text-primary flex items-center gap-1.5">
            <HardDrive className="w-3.5 h-3.5 text-primary" />
            {driveInfo.drive || '–'}
          </span>
        </div>
        <div className="p-3 rounded-xl bg-white/5 border border-white/10">
          <span className="text-text-secondary block mb-1">Filesystem</span>
          <span className={`font-semibold ${driveInfo.is_portable_fs ? 'text-warning' : 'text-success'}`}>
            {driveInfo.fs_type}
          </span>
        </div>
        <div className="p-3 rounded-xl bg-white/5 border border-white/10">
          <span className="text-text-secondary block mb-1">LanceDB Mode</span>
          <span className={`font-semibold ${driveInfo.lancedb_mode === 'split_brain' ? 'text-success' : 'text-text-primary'}`}>
            {driveInfo.lancedb_mode === 'split_brain' ? 'Split-Brain ✓' : 'Portable'}
          </span>
        </div>
        <div className="p-3 rounded-xl bg-white/5 border border-white/10">
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

function ResetSection({ onRestartOnboarding, onFullReset }: { onRestartOnboarding: () => void; onFullReset: () => void }) {
  return (
    <div className="glass p-6 rounded-2xl border border-error/10 bg-error/5">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-error/10 rounded-xl">
          <RefreshCcw className="w-6 h-6 text-error" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-text-primary">Showcase & Reset</h2>
          <p className="text-sm text-text-secondary mt-1">
            Use these options to prepare the app for a demonstration or fresh start.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap gap-4">
        <button
          onClick={onRestartOnboarding}
          className="glass-button !text-text-primary hover:bg-white/10 !py-2 !px-4 gap-2 border border-white/10"
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

export function SettingsPage() {
  const { data: authStatus, refetch: refetchAuth } = useApi(getAuthStatus, { cacheKey: 'auth-status' })
  const { data: localModels } = useApi(getLocalModels, { cacheKey: 'local-models' })
  const { data: sysInfo } = useApi(getSystemInfo, { cacheKey: 'system-info' })
  const { refetch: refetchPrefs } = useApi(getLLMPreferences, { cacheKey: 'llm-prefs' })
  const { data: driveInfo } = useApi(getDriveInfo, { cacheKey: 'drive-info' })

  const [message, setMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [savingPrefs, setSavingPrefs] = useState(false)
  const [purgingCache, setPurgingCache] = useState(false)

  useEffect(() => {
    const refreshAuth = () => {
      void refetchAuth()
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refreshAuth()
      }
    }

    globalThis.addEventListener('focus', refreshAuth)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      globalThis.removeEventListener('focus', refreshAuth)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [refetchAuth])

  const handleConnectGoogle = async () => {
    try {
      setMessage({ type: 'ok', text: 'Google sign-in opened in your browser. Return here when it finishes.' })
      await launchGoogleAuth()
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Could not launch Google sign-in' })
    }
  }

  const handleDisconnect = async () => {
    try {
      await disconnectAuth()
      invalidateCache('auth-status')
      refetchAuth()
      setMessage({ type: 'ok', text: 'Disconnected account.' })
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Disconnection failed' })
    }
  }

  const handleSavePrefs = async (prefs: LLMPreferences) => {
    setSavingPrefs(true)
    try {
      await setLLMPreferences(prefs)
      invalidateCache('llm-prefs')
      refetchPrefs()
      setMessage({ type: 'ok', text: 'LLM preferences saved.' })
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Failed to save preferences' })
    } finally {
      setSavingPrefs(false)
    }
  }

  const handleResetOnboarding = () => {
    if (!confirm('This will restart the onboarding flow. You can use it for your showcase. Proceed?')) return
    localStorage.removeItem('pma_setup_complete')
    globalThis.location.href = '/setup'
  }

  const handleFullReset = async () => {
    if (!confirm('CAUTION: This will clear your entire index AND reset onboarding. This cannot be undone. Proceed?')) return
    try {
      await clearIndex()
      localStorage.removeItem('pma_setup_complete')
      globalThis.location.href = '/setup'
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Reset failed' })
    }
  }

  const handlePurgeCache = async () => {
    if (!confirm('This will delete the local LanceDB vector cache. The backend will rebuild it from SQLite on next restart. Proceed?')) return
    setPurgingCache(true)
    try {
      const res = await purgeHostCache()
      setMessage({ type: 'ok', text: res.message })
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Purge failed' })
    } finally {
      setPurgingCache(false)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6 animate-fade-in-up custom-scrollbar">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3">
            <Settings className="w-7 h-7 text-primary" />
            Settings
          </h1>
          <p className="text-text-secondary mt-1 text-sm">
            Configure integrations and application preferences
          </p>
        </div>
      </div>

      {/* Message banner */}
      {message && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-xl text-sm transition-all duration-300 ${message.type === 'ok' ? 'bg-success/20 text-success' : 'bg-error/20 text-error'}`}>
          {message.type === 'ok' ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {message.text}
        </div>
      )}

      <AuthSection
        authStatus={authStatus}
        onConnect={handleConnectGoogle}
        onDisconnect={handleDisconnect}
      />

      <LocalModelsSection localModels={localModels} />

      <LLMPreferencesSection
        localModels={localModels}
        onSave={handleSavePrefs}
        saving={savingPrefs}
      />

      <StorageSection sysInfo={sysInfo} />

      <SplitBrainSection
        driveInfo={driveInfo}
        onPurge={handlePurgeCache}
        purging={purgingCache}
      />

      <ResetSection
        onRestartOnboarding={handleResetOnboarding}
        onFullReset={handleFullReset}
      />
    </div>
  )
}
