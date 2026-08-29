/**
 * Extracted from SettingsPage.tsx, which had grown to 1341 lines holding ten
 * unrelated section components in one unbroken scroll. Behaviour is unchanged;
 * only the file boundary moved.
 */
import { useState } from 'react'
import { Cpu, RefreshCcw, Play, Download } from 'lucide-react'
import { useApi, invalidateCache } from '../../useApi'
import { getProviderLaunchStatus, launchProvider, type LocalModelDetection } from '../../api'
import { CACHE_KEYS } from '../../cacheKeys'

/**
 * Start button for a provider PMA can launch itself (Ollama / LM Studio).
 * Falls back to the plain "make sure it's running" hint whenever the backend can't
 * tell us anything useful, so the card is never empty.
 */
export function StartLocalProviderButton({ providerId, displayName, offlineHint, onStarted }: Readonly<{
  providerId: string
  displayName: string
  offlineHint: string
  onStarted: () => void
}>) {
  const cacheKey = `launch-status-${providerId}`
  const { data: status, refetch: refetchStatus } = useApi(
    () => getProviderLaunchStatus(providerId),
    { cacheKey }
  )
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleStart = async () => {
    setStarting(true)
    setError(null)
    try {
      const res = await launchProvider(providerId)
      if (res.ok) {
        invalidateCache(CACHE_KEYS.localModels)
        invalidateCache(CACHE_KEYS.providersList)
        onStarted()
      } else {
        setError(res.message)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : `Could not start ${displayName}.`)
    } finally {
      setStarting(false)
      invalidateCache(cacheKey)
      refetchStatus()
    }
  }

  if (!status?.supported) {
    return <p className="text-sm text-text-secondary">{offlineHint}</p>
  }

  if (!status.installed) {
    return (
      <div className="flex flex-col gap-1.5">
        <p className="text-sm text-text-secondary">{displayName} isn't installed on this machine.</p>
        {status.install_url && (
          <a
            href={status.install_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-primary hover:underline"
          >
            <Download className="w-3.5 h-3.5" /> Install {displayName}
          </a>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        onClick={handleStart}
        disabled={starting}
        className="glass-button !bg-primary/10 border border-primary/20 text-primary hover:!bg-primary/20 px-3 py-2 rounded-xl text-xs font-semibold flex items-center justify-center gap-1.5 disabled:opacity-60 w-fit"
      >
        {starting ? (
          <RefreshCcw className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <Play className="w-3.5 h-3.5" />
        )}
        {starting ? 'Starting…' : `Start ${displayName}`}
      </button>
      {starting && (
        <span className="text-xs text-text-secondary">This can take up to a minute.</span>
      )}
      {error && !starting && (
        <span className="text-xs text-danger font-medium">{error}</span>
      )}
    </div>
  )
}

function LocalProviderCard({ providerId, displayName, emoji, detection, emptyHint, offlineHint, onStarted }: Readonly<{
  providerId: string
  displayName: string
  emoji: string
  detection?: { detected: boolean; models: string[] }
  emptyHint: string
  offlineHint: string
  onStarted: () => void
}>) {
  const detected = detection?.detected ?? false

  return (
    <div className="p-4 rounded-xl border border-primary/5 bg-surface backdrop-blur-md">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-text-primary flex items-center gap-2">
          <span className="text-xl">{emoji}</span> {displayName}
        </h3>
        {detected ? (
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
      {detected ? (
        <div className="flex flex-wrap gap-2 mt-2">
          {detection?.models.map(m => (
            <span key={m} className="px-2 py-1 text-xs font-medium bg-primary/10 text-primary rounded-md border border-primary/20">
              {m}
            </span>
          ))}
          {detection?.models.length === 0 && (
            <span className="text-sm text-text-secondary">{emptyHint}</span>
          )}
        </div>
      ) : (
        <StartLocalProviderButton
          providerId={providerId}
          displayName={displayName}
          offlineHint={offlineHint}
          onStarted={onStarted}
        />
      )}
    </div>
  )
}

export function LocalModelsSection({ localModels, onStarted }: Readonly<{
  localModels?: LocalModelDetection
  onStarted: () => void
}>) {
  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-primary/10 rounded-xl">
          <Cpu className="w-6 h-6 text-primary" />
        </div>
        <div>
          <h2 className="font-serif text-lg font-medium text-text-primary">Local LLM Auto-Detection</h2>
          <p className="text-sm text-text-secondary mt-1 max-w-lg">
            Offline models running on your machine are detected automatically.
            The backend will cascade to these if Google Gemini is unavailable or not configured.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <LocalProviderCard
          providerId="ollama"
          displayName="Ollama"
          emoji="🦙"
          detection={localModels?.ollama}
          emptyHint="Running, but no models installed."
          offlineHint="Ensure Ollama is running on localhost:11434."
          onStarted={onStarted}
        />
        <LocalProviderCard
          providerId="lm_studio"
          displayName="LM Studio"
          emoji="🖥️"
          detection={localModels?.lm_studio}
          emptyHint="Running, but no model loaded."
          offlineHint="Ensure LM Studio's Local Server is running on localhost:1234."
          onStarted={onStarted}
        />
      </div>
    </div>
  )
}
