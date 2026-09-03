import { useState, useEffect } from 'react'
import { Shield, ArrowRight, CheckCircle2, ChevronRight, HardDrive, AlertTriangle, Save, Library } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { useApi, invalidateCache } from '../useApi'
import {
    getLocalModels, getDriveInfo, enableSplitBrain, getProviders, setProviderKey,
    getProviderSettings, setProviderSettings, seedDemo, type ProviderStatus
} from '../api'
import { CACHE_KEYS } from '../cacheKeys'
import { Button, Panel, Skeleton } from '../components/ui'
import { ProviderIcon } from '../providers/icons'

// Which providers onboarding leads with, and in what order. The list used to
// carry hardcoded ids AND display names, which drifted from PROVIDER_REGISTRY
// and - more importantly - omitted spec.kind, the field that decides whether
// cloud consent applies. Names and kinds now come from the server; only the
// curation stays local. Everything else is one link away.
const SETUP_FEATURED_IDS = ['gemini', 'groq', 'nvidia_nim', 'openrouter'] as const

interface SetupProvider {
    id: string
    name: string
}

function featuredProviders(providers: ProviderStatus[] | null | undefined): SetupProvider[] {
    if (!providers) return []
    return SETUP_FEATURED_IDS.map(id => providers.find(p => p.spec.id === id))
        .filter((p): p is ProviderStatus => !!p)
        .map(p => ({
            id: p.spec.id,
            name: p.spec.display_name || p.spec.id,
        }))
}

/**
 * A row in the engine list.
 *
 * `border-edge` rather than a tinted fill: this row contains a control, and
 * WCAG 1.4.11 wants 3:1 on a boundary that identifies one. A `bg-primary/10`
 * wash reads as decoration and measures as nothing.
 */
function ApiKeyInput({ provider }: { provider: SetupProvider }) {
    const { data: providers, refetch } = useApi(getProviders, { cacheKey: CACHE_KEYS.providersList })
    const pData = providers?.find(p => p.spec.id === provider.id)
    const [key, setKey] = useState('')
    const [saving, setSaving] = useState(false)
    const [saveError, setSaveError] = useState<string | null>(null)
    // "Update" used to call `setKey('')` and `setSaving(false)` - both already
    // their current values - while the branch below still keyed off
    // `pData.is_set`, so the control did nothing at all and a stored key could
    // not be replaced from onboarding. This is the flag that actually reopens
    // the field.
    const [editing, setEditing] = useState(false)

    const handleSave = async () => {
        if (!key) return
        setSaving(true)
        setSaveError(null)
        try {
            await setProviderKey(provider.id, key)
            setKey('')
            setEditing(false)
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
        <div
            className={`p-4 rounded-md border flex items-center justify-between gap-4 bg-surface ${
                pData?.is_set ? 'border-success' : 'border-edge'
            }`}
        >
            <div className="flex items-center gap-3 w-1/3">
                <ProviderIcon id={provider.id} className="w-4 h-4 shrink-0" />
                <span className="font-medium truncate">{provider.name}</span>
            </div>

            {pData?.is_set && !editing ? (
                <div className="flex-1 flex justify-end items-center gap-4">
                    <span className="font-mono text-[11px] text-text-tertiary">
                        {pData.stored_in === 'keyring' ? 'stored in keyring' : 'stored in env'}
                    </span>
                    <span className="text-success text-sm flex items-center gap-1 font-medium">
                        <CheckCircle2 className="w-4 h-4" /> Ready
                    </span>
                    {/* A key held in .env is not ours to replace - the same
                        rule ProvidersPage enforces by disabling save for it. */}
                    {pData.stored_in !== 'env' && (
                        <Button
                            variant="quiet"
                            size="sm"
                            onClick={() => { setKey(''); setSaveError(null); setEditing(true) }}
                        >
                            Update
                        </Button>
                    )}
                </div>
            ) : (
                <div className="flex-1 flex flex-col gap-2">
                    <div className="flex items-center gap-2">
                        <input
                            type="password"
                            aria-label={`${provider.name} API key`}
                            name={`${provider.id}-api-key`}
                            // An API key is not a login credential: without this
                            // the browser offers to save it to the password
                            // manager under the PMA origin.
                            autoComplete="off"
                            spellCheck={false}
                            placeholder="Paste your key, e.g. sk-…"
                            value={key}
                            onChange={e => setKey(e.target.value)}
                            className="glass-input flex-1 py-1.5 text-sm rounded-sm"
                        />
                        <Button
                            variant="secondary"
                            size="sm"
                            onClick={handleSave}
                            disabled={!key}
                            loading={saving}
                            icon={<Save className="w-4 h-4" />}
                        >
                            Save
                        </Button>
                        {editing && (
                            <Button
                                variant="quiet"
                                size="sm"
                                onClick={() => { setKey(''); setSaveError(null); setEditing(false) }}
                            >
                                Cancel
                            </Button>
                        )}
                    </div>
                    {/* The visible error stays conditional so it costs no
                        layout when absent; the announcement comes from a
                        region that is always mounted, because an aria-live
                        region inserted together with its text announces
                        nothing. Same shape as LibraryPage's status line. */}
                    <span className="sr-only" aria-live="polite">{saveError ?? ''}</span>
                    {saveError && (
                        <div className="text-xs text-error font-medium animate-fade-in">
                            {saveError}
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

/**
 * The step marks.
 *
 * Two filled progress bars were the generic move and carried no name. These are
 * catalogue marks in the same idiom as the nav's label slips, so the user reads
 * where they are rather than how full a bar is.
 */
function StepMarks({ step }: Readonly<{ step: number }>) {
    const steps = [
        { n: 1, mark: 'I · ENGINE', name: 'Connect a model' },
        { n: 2, mark: 'II · FOLDERS', name: 'Give it something to read' },
    ]
    return (
        <ol className="flex gap-8 border-b border-rule pb-3 mb-8 m-0 p-0 list-none">
            {steps.map(s => (
                <li key={s.n} aria-current={step === s.n ? 'step' : undefined}>
                    <div
                        className={`font-mono text-[10px] tracking-[0.16em] uppercase ${
                            step >= s.n ? 'text-primary' : 'text-text-tertiary'
                        }`}
                    >
                        {s.mark}
                    </div>
                    <div
                        className={`font-serif text-base leading-tight ${
                            step >= s.n ? 'text-text-primary' : 'text-text-tertiary'
                        }`}
                    >
                        {s.name}
                    </div>
                </li>
            ))}
        </ol>
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
        // The blur blobs that used to sit here (two 24rem `blur-[120px]`
        // `mix-blend-screen` washes) were the last mesh-glow in the app, and
        // 06_DESIGN_SYSTEM.md lists exactly that as out. The ground is the design.
        <div className="fixed inset-0 bg-background flex flex-col items-center p-6 z-50 overflow-y-auto">

            <Panel className="my-auto w-full max-w-2xl p-6 sm:p-10 animate-fade-in-up shadow-lg relative flex flex-col shrink-0">

                {/* Header */}
                <div className="flex items-center gap-4 mb-8">
                    {/* Flat brass. This was a `from-primary to-accent-blue` gradient
                        under a coloured glow, which is two accents and a halo. */}
                    <div className="w-12 h-12 bg-plate rounded-md flex items-center justify-center shrink-0 shadow-md">
                        <Library className="w-6 h-6 text-on-plate" />
                    </div>
                    <div className="min-w-0">
                        <div className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary">
                            Personal Memory Assistant
                        </div>
                        <h1 className="font-serif text-3xl font-normal text-text-primary tracking-tight leading-tight">
                            Welcome to PMA
                        </h1>
                    </div>
                </div>

                <p className="text-text-secondary max-w-[52ch] mb-8 mt-0">
                    Everything stays on this machine. Point PMA at a model, then at your files.
                </p>

                <StepMarks step={step} />

                {/* Step 1: Connect Engine */}
                {step === 1 && (
                    <div className="flex flex-col gap-4 animate-fade-in-right">

                        {isLoading && (!providers || !localModels) && (
                            <div className="flex flex-col gap-4">
                                <Skeleton className="h-40" />
                                <div className="grid grid-cols-2 gap-4">
                                    <Skeleton className="h-16" />
                                    <Skeleton className="h-16" />
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
                            <div className="p-4 rounded-md border border-warning bg-surface flex flex-col gap-3">
                                <div className="flex items-start gap-3">
                                    <AlertTriangle className="w-5 h-5 text-warning flex-shrink-0 mt-0.5" />
                                    <div>
                                        <h4 className="font-medium text-warning flex items-center gap-2">
                                            <HardDrive className="w-4 h-4" /> Incompatible Storage Detected
                                        </h4>
                                        <p className="text-sm text-text-secondary mt-1">
                                            Drive <strong>{driveInfo.drive}</strong> is formatted as{' '}
                                            <strong>{driveInfo.fs_type}</strong>. LanceDB is unstable on portable filesystems.
                                        </p>
                                    </div>
                                </div>

                                <div className="ml-8 border-t border-rule pt-3 mt-1">
                                    <span className="sr-only" aria-live="polite">{splitBrainError ?? ''}</span>
                                    <Button
                                        variant="secondary"
                                        size="sm"
                                        onClick={handleEnableSplitBrain}
                                        loading={isEnablingSplitBrain}
                                    >
                                        Enable Split-Brain Mode
                                    </Button>
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
                            <div className="p-4 rounded-md border border-success bg-surface flex items-start gap-3 animate-fade-in">
                                <CheckCircle2 className="w-5 h-5 text-success flex-shrink-0 mt-0.5" />
                                <div>
                                    <h4 className="font-medium text-success flex items-center gap-2">
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
                            <div className="p-3 rounded-md border border-success bg-surface flex items-center gap-3">
                                <CheckCircle2 className="w-4 h-4 text-success flex-shrink-0" />
                                <p className="text-sm text-success font-medium">
                                    Split-Brain mode active — portable drive ({driveInfo.fs_type}) is safe.
                                </p>
                            </div>
                        )}

                        {(!isLoading || providers) && (
                            <>
                                <div
                                    className={`p-5 rounded-xl border bg-surface ${
                                        isConnected ? 'border-success' : 'border-rule'
                                    }`}
                                >
                                    <div className="flex items-center justify-between mb-4">
                                        <div>
                                            <h3 className="font-serif text-lg font-medium flex items-center gap-3">
                                                Cloud models
                                                <span className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary">
                                                    secure keyring
                                                </span>
                                            </h3>
                                            <p className="text-sm text-text-secondary mt-1">Provide API keys for your preferred cloud models.</p>
                                        </div>
                                    </div>

                                    <div className="flex flex-col gap-3">
                                        {featured.map(p => <ApiKeyInput key={p.id} provider={p} />)}
                                    </div>

                                    {consentRequired && (
                                        // Was `bg-amber-500/10 border-amber-500/20 text-amber-800`
                                        // with an `text-amber-600` icon: raw palette values, not
                                        // tokens, so on the dark theme this was near-black text on
                                        // a near-black wash. `warning` is the measured token and
                                        // reads 8.18 on cabinet, 6.82 on paper.
                                        <div className="mt-4 p-3 bg-surface border border-warning rounded-md text-xs text-text-primary flex flex-col gap-2.5">
                                            <div className="flex items-start gap-2">
                                                <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
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
                                                    className="rounded-xs border-edge"
                                                />
                                                <span className="font-medium">I understand and consent to cloud data processing</span>
                                            </label>
                                        </div>
                                    )}

                                    <p className="mt-3 text-xs text-text-secondary">
                                        Using something else?{' '}
                                        <Link
                                            to="/settings/providers"
                                            onClick={completeSetup}
                                            className="text-primary underline underline-offset-4 hover:text-primary-light transition-colors"
                                        >
                                            See all providers
                                        </Link>
                                    </p>
                                </div>

                                <div className="flex items-center gap-4 my-2">
                                    <div className="flex-1 h-px bg-rule" />
                                    <span className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary">or auto-detected locals</span>
                                    <div className="flex-1 h-px bg-rule" />
                                </div>

                                <div className="grid grid-cols-2 gap-4">
                                    <div className={`p-4 rounded-md border bg-surface ${localModels?.ollama.detected ? 'border-success' : 'border-rule'}`}>
                                        <h4 className="font-medium mb-1 flex items-center gap-2">
                                            <ProviderIcon id="ollama" className="w-4 h-4" /> Ollama
                                        </h4>
                                        {localModels?.ollama.detected ? (
                                            <span className="text-success text-sm flex items-center gap-1"><CheckCircle2 className="w-4 h-4" /> Detected</span>
                                        ) : (
                                            <span className="text-text-secondary text-sm">Not detected on port 11434</span>
                                        )}
                                    </div>
                                    <div className={`p-4 rounded-md border bg-surface ${localModels?.lm_studio.detected ? 'border-success' : 'border-rule'}`}>
                                        <h4 className="font-medium mb-1 flex items-center gap-2">
                                            <ProviderIcon id="lm_studio" className="w-4 h-4" /> LM Studio
                                        </h4>
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
                            {/* The ready state used to add `animate-pulse ring-2 ring-primary
                                ring-offset-2`, i.e. a pulsing halo on an enabled button. The
                                button going from disabled to enabled already says it. */}
                            <Button
                                variant="plate"
                                onClick={() => setStep(2)}
                                disabled={!canProceed}
                            >
                                Continue <ArrowRight className="w-4 h-4" />
                            </Button>
                        </div>
                    </div>
                )}

                {/* Step 2: Complete */}
                {step === 2 && (
                    <div className="flex flex-col gap-6 animate-fade-in-right items-start">

                        <div className="w-12 h-12 bg-surface border border-edge rounded-md flex items-center justify-center text-success">
                            <Shield className="w-6 h-6" />
                        </div>

                        <div>
                            <h3 className="font-serif text-2xl font-normal">Model connected</h3>
                            <p className="text-text-secondary max-w-[52ch] mt-2 mb-0">
                                PMA can read now, but it has nothing to read yet. Give it a folder,
                                or start with the demo corpus.
                            </p>
                        </div>

                        {/* Setup used to end here with an empty index, on a screen whose
                            only action was "Go to Library". Both buttons now finish setup
                            AND leave; the demo is the bounded option, and indexing a real
                            folder is handed to Library, which already owns that flow and
                            its error states. */}
                        <div className="mt-2 flex flex-col sm:flex-row gap-4 w-full">
                            <Button
                                variant="plate"
                                className="flex-1"
                                onClick={() => void handleTryDemo()}
                                loading={seeding}
                            >
                                Try the demo corpus
                            </Button>
                            <Button
                                variant="secondary"
                                className="flex-1"
                                onClick={() => {
                                    completeSetup()
                                    navigate('/')
                                }}
                            >
                                Index my first folder
                                <ChevronRight className="w-4 h-4" />
                            </Button>
                        </div>
                    </div>
                )}

            </Panel>
        </div>
    )
}
