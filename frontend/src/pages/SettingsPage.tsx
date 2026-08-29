import { useState } from 'react'
import { Settings, CheckCircle2, AlertCircle } from 'lucide-react'
import { useApi, invalidateCache } from '../useApi'
import { getLocalModels, getSystemInfo, getLLMPreferences, setLLMPreferences, setProviderDefaultModel, clearIndex, getDriveInfo, purgeHostCache, type LLMPreferences } from '../api'
import { CACHE_KEYS } from '../cacheKeys'
import { LocalModelsSection } from './settings/LocalModels'
import { LLMPreferencesSection } from './settings/LLMPreferences'
import { OcrSection } from './settings/OcrSection'
import { StorageSection } from './settings/StorageSection'
import { SplitBrainSection } from './settings/SplitBrainSection'
import { DiagnosticsSection } from './settings/DiagnosticsSection'
import { ResetSection } from './settings/ResetSection'

export function SettingsPage() {
  const { data: localModels, refetch: refetchLocalModels } = useApi(getLocalModels, { cacheKey: CACHE_KEYS.localModels })
  const { data: sysInfo } = useApi(getSystemInfo, { cacheKey: CACHE_KEYS.systemInfo })
  const { refetch: refetchPrefs } = useApi(getLLMPreferences, { cacheKey: CACHE_KEYS.llmPreferences })
  const { data: driveInfo } = useApi(getDriveInfo, { cacheKey: CACHE_KEYS.driveInfo })

  const [message, setMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [savingPrefs, setSavingPrefs] = useState(false)
  const [purgingCache, setPurgingCache] = useState(false)



  const handleSavePrefs = async (prefs: LLMPreferences) => {
    setSavingPrefs(true)
    try {
      await setLLMPreferences(prefs)
      for (const [key, val] of Object.entries(prefs)) {
        if (key.endsWith('_model') && typeof val === 'string' && val) {
          const providerId = key.replace('_model', '')
          await setProviderDefaultModel(providerId, val).catch(() => {})
        }
      }
      invalidateCache(CACHE_KEYS.llmPreferences)
      invalidateCache(CACHE_KEYS.providersList)
      refetchPrefs()
      setMessage({ type: 'ok', text: 'LLM preferences saved.' })
    } catch (e) {
      setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Failed to save preferences' })
    } finally {
      setSavingPrefs(false)
    }
  }

  // Onboarding lives behind two independent localStorage keys - SetupPage's
  // 'pma_setup_complete' and TourOverlay's 'pma_tour_completed'. Clearing only
  // the first meant "Restart Onboarding" left the provider tour dismissed
  // forever.
  const clearOnboardingState = () => {
    localStorage.removeItem('pma_setup_complete')
    localStorage.removeItem('pma_tour_completed')
  }

  const handleResetOnboarding = () => {
    if (!confirm('This will restart the onboarding flow. You can use it for your showcase. Proceed?')) return
    clearOnboardingState()
    globalThis.location.href = '/setup'
  }

  const handleFullReset = async () => {
    if (!confirm('CAUTION: This will clear your entire index AND reset onboarding. This cannot be undone. Proceed?')) return
    try {
      await clearIndex()
      clearOnboardingState()
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
          <h1 className="font-serif text-2xl font-normal flex items-center gap-3">
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



      <LocalModelsSection localModels={localModels} onStarted={refetchLocalModels} />

      <LLMPreferencesSection
        onSave={handleSavePrefs}
        saving={savingPrefs}
      />

      <OcrSection />

      <StorageSection sysInfo={sysInfo} />

      <SplitBrainSection
        driveInfo={driveInfo}
        onPurge={handlePurgeCache}
        purging={purgingCache}
      />

      <DiagnosticsSection />

      <ResetSection
        onRestartOnboarding={handleResetOnboarding}
        onFullReset={handleFullReset}
      />
    </div>
  )
}
