import { useState, useEffect } from 'react'
import { Brain, Shield, ArrowRight, CheckCircle2, ChevronRight, HardDrive, AlertTriangle, Save, Loader2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { useApi, invalidateCache } from '../useApi'
import {
    getLocalModels, getDriveInfo, enableSplitBrain, getProviders, setProviderKey,
    getProviderSettings, setProviderSettings, seedDemo, type ProviderStatus
} from '../api'
import { CACHE_KEYS } from '../cacheKeys'

// Which providers onboarding leads with, and in what order. The list used to
// carry hardcoded ids AND display names, which drifted from PROVIDER_REGISTRY
// and - more importantly - omitted spec.kind, the field that decides whether
// cloud consent applies. Names and kinds now come from the server; only the
// curation stays local. Everything else is one link away.
const SETUP_FEATURED_IDS = ['gemini', 'groq', 'nvidia_nim', 'openrouter'] as const

const PROVIDER_ICONS: Record<string, string> = {
    gemini: '✨',
    groq: '⚡',
    nvidia_nim: '🟢',
    openrouter: '🌐',
}

interface SetupProvider {
    id: string
    name: string
    icon: string
}

function featuredProviders(providers: ProviderStatus[] | null | undefined): SetupProvider[] {
    if (!providers) return []
    return SETUP_FEATURED_IDS.map(id => providers.find(p => p.spec.id === id))
        .filter((p): p is ProviderStatus => !!p)
        .map(p => ({
            id: p.spec.id,
            name: p.spec.display_name || p.spec.id,
            icon: PROVIDER_ICONS[p.spec.id] || '🔌',
        }))
}

function ApiKeyInput({ provider }: { provider: SetupProvider }) {
    const { data: providers, refetch } = useApi(getProviders, { cacheKey: CACHE_KEYS.providersList })
    const pData = providers?.find(p => p.spec.id === provider.id)
    const [key, setKey] = useState('')
    const [saving, setSaving] = useState(false)
    const [saveError, setSaveError] = useState<string | null>(null)

    const handleSave = async () => {
        if (!key) return
        setSaving(true)
        setSaveError(null)
        try {
            await setProviderKey(provider.id, key)
            setKey('')
            await refetch()
            // Storing a cloud key is what creates the consent obligation, so the
            // gate below has to re-evaluate against the new chain.
            invalidateCache(CACHE_KEYS.providerSettings)
        } catch (e: any) {
            setSaveError(e.message || 'Failed to save API key')
        } finally {
            setSaving(false)
        }
    }

    return (
        <div className={`p-4 rounded-xl border flex items-center justify-between gap-4 ${pData?.is_set ? 'border-success bg-success/5' : 'border-primary/10 bg-surface'}`}>
            <div className="flex items-center gap-3 w-1/3">
                <span className="text-xl">{provider.icon}</span>
                <span className="font-semibold">{provider.name}</span>
            </div>
            
            {pData?.is_set ? (
                <div className="flex-1 flex justify-end items-center gap-4">
                    <span className="text-xs font-mono text-success opacity-80 bg-success/10 px-2 py-1 rounded">
                        {pData.stored_in === 'keyring' ? 'Stored in Keyring' : 'Stored in Env'}
                    </span>
                    <span className="text-success text-sm flex items-center gap-1 font-medium">
                        <CheckCircle2 className="w-4 h-4" /> Ready
                    </span>
                    <button onClick={() => { setKey(''); setSaving(false) }} className="text-xs text-text-secondary hover:text-primary transition-colors">
                        Update
                    </button>
                </div>
            ) : (
                <div className="flex-1 flex flex-col gap-2">
                    <div className="flex items-center gap-2">
                        <input 
                            type="password"
                            placeholder="Enter API Key..."
                            value={key}
                            onChange={e => setKey(e.target.value)}
                            className="flex-1 bg-background/50 border border-primary/20 rounded-lg px-3 py-1.5 text-sm outline-none focus:border-primary/50 transition-colors"
                        />
                        <button 
                            onClick={handleSave} 
                            disabled={saving || !key}
                            className="glass-button !bg-primary/10 !text-primary hover:!bg-primary/20 px-3 py-1.5 rounded-lg text-sm disabled:opacity-50 flex items-center gap-1"
                        >
                            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                            Save
                        </button>
                    </div>
                    {saveError && (
                        <div className="text-xs text-danger font-medium animate-fade-in">
                            {saveError}
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

export function SetupPage() {
    const navigate = useNavigate()
    const { data: providers, refetch: refetchProviders, loading: providersLoading } = useApi(getProviders, { cacheKey: CACHE_KEYS.providersList })
    const { data: localModels, loading: localModelsLoading } = useApi(getLocalModels, { cacheKey: CACHE_KEYS.localModels })
    const { data: driveInfo, loading: driveLoading } = useApi(getDriveInfo, { cacheKey: CACHE_KEYS.driveInfo })
    const { data: routingSettings, refetch: refetchRouting } = useApi(getProviderSettings, { cacheKey: CACHE_KEYS.providerSettings })

    const isLoading = providersLoading || localModelsLoading || driveLoading

    const featured = featuredProviders(providers)
    // Server-computed from the same predicate as the dispatch gate. Setup used
    // to finish with a cloud key stored and consent never asked, after which
    // the very first question failed with the remedy two pages away.
    const consentRequired = routingSettings?.consent_required === true
    const [savingConsent, setSavingConsent] = useState(false)

    const handleConsent = async (checked: boolean) => {
        setSavingConsent(true)
        try {
            await setProviderSettings({ cloud_privacy_consent: checked })
            invalidateCache(CACHE_KEYS.providerSettings)
            await refetchRouting()
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Could not save your consent choice.')
        } finally {
            setSavingConsent(false)
        }
    }

    const completeSetup = () => localStorage.setItem('pma_setup_complete', 'true')

    const [seeding, setSeeding] = useState(false)
    const handleTryDemo = async () => {
        setSeeding(true)
        try {
            await seedDemo()
            toast.success('Demo corpus is indexing — ask it something.')
        } catch (e) {
            // Never strand the user on this screen because a sample failed.
            toast.warning(e instanceof Error ? e.message : 'Could not load the demo corpus.')
        } finally {
            setSeeding(false)
            completeSetup()
            navigate('/')
        }
    }

    const [step, setStep] = useState(1)

    const [isEnablingSplitBrain, setIsEnablingSplitBrain] = useState(false)
    const [requiresRestart, setRequiresRestart] = useState(false)
    const [splitBrainError, setSplitBrainError] = useState<string | null>(null)

    useEffect(() => {
        const refreshProviders = () => {
            void refetchProviders()
        }

        const handleVisibilityChange = () => {
            if (document.visibilityState === 'visible') {
                refreshProviders()
            }
        }

        window.addEventListener('focus', refreshProviders)
        document.addEventListener('visibilitychange', handleVisibilityChange)
        return () => {
            window.removeEventListener('focus', refreshProviders)
            document.removeEventListener('visibilitychange', handleVisibilityChange)
        }
    }, [refetchProviders])




    const handleEnableSplitBrain = async () => {
        setIsEnablingSplitBrain(true)
        setSplitBrainError(null)
        try {
            await enableSplitBrain()
            setRequiresRestart(true)
        } catch (error: any) {
            setSplitBrainError(error.message || 'Failed to enable Split-Brain mode')
        } finally {
            setIsEnablingSplitBrain(false)
        }
    }

    const isConnected = providers?.some(p => p.is_set)
    const hasLocalModels = localModels?.ollama.detected || localModels?.lm_studio.detected

    // Block setup if drive is exFAT/FAT32 but mode is not split_brain, or if restart is required
    const isDriveConfigSafe = driveInfo
        ? !(driveInfo.is_portable_fs && driveInfo.lancedb_mode !== 'split_brain')
        : true

    const canProceed = (isConnected || hasLocalModels) && isDriveConfigSafe && !requiresRestart && !consentRequired

    return (
        <div className="fixed inset-0 bg-background flex flex-col items-center p-6 z-50 overflow-y-auto">

            {/* Background glow effects */}
            <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/20 blur-[120px] rounded-full mix-blend-screen pointer-events-none" />
            <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-accent-blue/20 blur-[120px] rounded-full mix-blend-screen pointer-events-none" />

            <div className="my-auto w-full max-w-2xl glass rounded-3xl p-6 sm:p-10 animate-fade-in-up border border-primary/10 shadow-2xl relative z-10 flex flex-col shrink-0">

                {/* Header */}
                <div className="flex flex-col items-center text-center mb-10">
                    <div className="w-16 h-16 bg-gradient-to-br from-primary to-accent-blue rounded-2xl flex items-center justify-center mb-6 shadow-lg shadow-primary/20">
                        <Brain className="w-8 h-8 text-on-plate" />
                    </div>
                    <h1 className="font-serif text-3xl font-normal text-text-primary tracking-tight">Welcome to PMA</h1>
                    <p className="text-text-secondary mt-2 max-w-sm">
                        Your offline-first personal memory assistant. Let&apos;s get your intelligence engine connected.
                    </p>
                </div>

                {/* Steps */}
                <div className="flex items-center gap-4 mb-8 px-8">
                    <div className={`flex-1 h-1.5 rounded-full ${step >= 1 ? 'bg-primary' : 'bg-primary/20'}`} />
                    <div className={`flex-1 h-1.5 rounded-full ${step >= 2 ? 'bg-primary' : 'bg-primary/20'}`} />
                </div>

                {/* Step 1: Connect Engine */}
                {step === 1 && (
                    <div className="flex flex-col gap-4 animate-fade-in-right">

                        {isLoading && (!providers || !localModels) && (
                            <div className="flex flex-col gap-4 animate-pulse">
                                <div className="h-40 bg-primary/5 rounded-2xl border border-primary/10" />
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="h-16 bg-primary/5 rounded-xl border border-primary/10" />
                                    <div className="h-16 bg-primary/5 rounded-xl border border-primary/10" />
                                </div>
                            </div>
                        )}

                        {/* Gated on data, not on fetching state. `isLoading` is
                            `isLoading || isFetching`, and the focus/visibilitychange
                            listeners above refetch on every alt-tab - so `!isLoading`
                            made this storage-compatibility warning blink out exactly
                            when the user returned to the page. `driveInfo` presence is
                            the real guard, and isDriveConfigSafe already defaults to
                            true while it is absent. */}
                        {!isDriveConfigSafe && driveInfo && !requiresRestart && (
                            <div className="p-4 rounded-xl border border-warning bg-warning/10 flex flex-col gap-3">
                                <div className="flex items-start gap-3">
                                    <AlertTriangle className="w-5 h-5 text-warning flex-shrink-0 mt-0.5" />
                                    <div>
                                        <h4 className="font-semibold text-warning flex items-center gap-2">
                                            <HardDrive className="w-4 h-4" /> Incompatible Storage Detected
                                        </h4>
                                        <p className="text-sm text-text-secondary mt-1">
                                            Drive <strong>{driveInfo.drive}</strong> is formatted as{' '}
                                            <strong>{driveInfo.fs_type}</strong>. LanceDB is unstable on portable filesystems.
                                        </p>
                                    </div>
                                </div>
                                
                                <div className="ml-8 border-t border-warning/20 pt-3 mt-1">
                                    <button 
                                        onClick={handleEnableSplitBrain}
                                        disabled={isEnablingSplitBrain}
                                        className="glass-button !bg-warning/20 !text-warning border border-warning/50 hover:!bg-warning/30 px-4 py-2 text-sm disabled:opacity-50 transition-colors"
                                    >
                                        {isEnablingSplitBrain ? 'Enabling...' : 'Enable Split-Brain Mode'}
                                    </button>
                                    {splitBrainError && (
                                        <p className="text-error text-xs mt-2">{splitBrainError}</p>
                                    )}
                                    <p className="text-xs text-text-secondary mt-2">
                                        This will configure PMA to safely store its cache on your local computer, allowing the portable drive to function correctly.
                                    </p>
                                </div>
                            </div>
                        )}

                        {/* Restart Required Banner */}
                        {requiresRestart && (
                            <div className="p-4 rounded-xl border border-success bg-success/10 flex items-start gap-3 animate-fade-in">
                                <CheckCircle2 className="w-5 h-5 text-success flex-shrink-0 mt-0.5" />
                                <div>
                                    <h4 className="font-semibold text-success flex items-center gap-2">
                                        Split-Brain Mode Enabled
                                    </h4>
                                    <p className="text-sm text-text-secondary mt-1">
                                        The configuration has been updated successfully. Please <strong>fully close and restart the application</strong> to apply the changes.
                                    </p>
                                </div>
                            </div>
                        )}

                        {/* Drive OK badge — shown when split_brain is active on portable drive */}
                        {/* Same reasoning as the warning above: `driveInfo?.is_portable_fs`
                            already requires the data to have arrived. */}
                        {isDriveConfigSafe && driveInfo?.is_portable_fs && (
                            <div className="p-3 rounded-xl border border-success bg-success/5 flex items-center gap-3">
                                <CheckCircle2 className="w-4 h-4 text-success flex-shrink-0" />
                                <p className="text-sm text-success font-medium">
                                    Split-Brain mode active — portable drive ({driveInfo.fs_type}) is safe.
                                </p>
                            </div>
                        )}

                        {(!isLoading || providers) && (
                            <>
                                <div className={`p-5 rounded-2xl border transition-all duration-300 ${isConnected ? 'border-success bg-success/5' : 'border-primary/10 bg-surface'}`}>
                                    <div className="flex items-center justify-between mb-4">
                                        <div>
                                            <h3 className="font-bold text-lg flex items-center gap-2">
                                                Cloud Intelligence <span className="text-xs ml-2 bg-primary/10 text-primary px-2 py-0.5 rounded-full">Secure Keyring</span>
                                            </h3>
                                            <p className="text-sm text-text-secondary mt-1">Provide API keys for your preferred cloud models.</p>
                                        </div>
                                    </div>

                                    <div className="flex flex-col gap-3">
                                        {featured.map(p => <ApiKeyInput key={p.id} provider={p} />)}
                                    </div>

                                    {consentRequired && (
                                        <div className="mt-4 p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-xs text-amber-800 flex flex-col gap-2.5">
                                            <div className="flex items-start gap-2">
                                                <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
                                                <span>
                                                    <strong>Privacy Notice:</strong>{' '}
                                                    {routingSettings?.cloud_privacy_notice ||
                                                        'Free-tier cloud dispatches may use data inputs for model training/improvement per provider terms and are restricted for EEA, Switzerland, and UK users.'}
                                                </span>
                                            </div>
                                            <label className="flex items-center gap-2 pl-6 cursor-pointer select-none">
                                                <input
                                                    type="checkbox"
                                                    checked={!!routingSettings?.cloud_privacy_consent}
                                                    disabled={savingConsent}
                                                    onChange={e => void handleConsent(e.target.checked)}
                                                    className="rounded border-amber-500/40"
                                                />
                                                <span className="font-medium">I understand and consent to cloud data processing</span>
                                            </label>
                                        </div>
                                    )}

                                    <p className="mt-3 text-xs text-text-secondary">
                                        Using something else?{' '}
                                        <button
                                            onClick={() => { completeSetup(); navigate('/settings/providers') }}
                                            className="underline hover:text-primary transition-colors"
                                        >
                                            See all providers
                                        </button>
                                    </p>
                                </div>

                                <div className="flex items-center gap-4 my-2">
                                    <div className="flex-1 h-px bg-primary/10" />
                                    <span className="text-xs text-text-secondary font-medium uppercase tracking-wider">or auto-detected locals</span>
                                    <div className="flex-1 h-px bg-primary/10" />
                                </div>

                                <div className="grid grid-cols-2 gap-4">
                                    <div className={`p-4 rounded-xl border ${localModels?.ollama.detected ? 'border-success bg-success/5' : 'border-primary/10 bg-surface'}`}>
                                        <h4 className="font-semibold mb-1 flex items-center gap-2">🦙 Ollama</h4>
                                        {localModels?.ollama.detected ? (
                                            <span className="text-success text-sm flex items-center gap-1"><CheckCircle2 className="w-4 h-4" /> Detected</span>
                                        ) : (
                                            <span className="text-text-secondary text-sm">Not detected on port 11434</span>
                                        )}
                                    </div>
                                    <div className={`p-4 rounded-xl border ${localModels?.lm_studio.detected ? 'border-success bg-success/5' : 'border-primary/10 bg-surface'}`}>
                                        <h4 className="font-semibold mb-1 flex items-center gap-2">🖥️ LM Studio</h4>
                                        {localModels?.lm_studio.detected ? (
                                            <span className="text-success text-sm flex items-center gap-1"><CheckCircle2 className="w-4 h-4" /> Detected</span>
                                        ) : (
                                            <span className="text-text-secondary text-sm">Not detected on port 1234</span>
                                        )}
                                    </div>
                                </div>
                            </>
                        )}

                        <div className="mt-8 flex justify-end">
                            <button
                                onClick={() => setStep(2)}
                                disabled={!canProceed}
                                className={`glass-button !bg-plate !text-on-plate hover:opacity-90 gap-2 px-8 py-3 disabled:opacity-50 disabled:cursor-not-allowed transition-all ${
                                    canProceed && isConnected ? 'animate-pulse ring-2 ring-primary ring-offset-2 ring-offset-background' : ''
                                }`}
                            >
                                Continue <ArrowRight className="w-5 h-5" />
                            </button>
                        </div>
                    </div>
                )}

                {/* Step 2: Complete */}
                {step === 2 && (
                    <div className="flex flex-col gap-6 animate-fade-in-right text-center items-center">

                        <div className="w-16 h-16 bg-success/10 rounded-full flex items-center justify-center text-success mb-2">
                            <Shield className="w-8 h-8" />
                        </div>

                        <h3 className="font-bold text-2xl">Engine Assigned</h3>
                        <p className="text-text-secondary max-w-sm">
                            Your memory assistant is now intelligent — but it has nothing to remember yet.
                            Give it something to read.
                        </p>

                        {/* Setup used to end here with an empty index, on a screen whose
                            only action was "Go to Library". Both buttons now finish setup
                            AND leave; the demo is the bounded option, and indexing a real
                            folder is handed to Library, which already owns that flow and
                            its error states. */}
                        <div className="mt-6 flex flex-col sm:flex-row gap-4 w-full">
                            <button
                                onClick={() => void handleTryDemo()}
                                disabled={seeding}
                                className="flex-1 glass-button !bg-plate !text-on-plate justify-center py-4 text-lg font-semibold hover:shadow-lg transition-all disabled:opacity-60"
                            >
                                {seeding ? <Loader2 className="w-5 h-5 animate-spin" /> : null}
                                Try the demo corpus
                            </button>
                            <button
                                onClick={() => {
                                    completeSetup()
                                    navigate('/')
                                }}
                                className="flex-1 glass-button justify-center py-4 text-lg font-semibold hover:shadow-lg transition-all"
                            >
                                Index my first folder
                                <ChevronRight className="w-5 h-5 ml-1" />
                            </button>
                        </div>
                    </div>
                )}

            </div>
        </div>
    )
}
