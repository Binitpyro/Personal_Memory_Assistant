import { useState, useEffect } from 'react'
import { Brain, Shield, ArrowRight, CheckCircle2, ChevronRight, Key, HardDrive, AlertTriangle } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useApi } from '../useApi'
import { getAuthStatus, getLocalModels, launchGoogleAuth, getDriveInfo, enableSplitBrain } from '../api'

export function SetupPage() {
    const navigate = useNavigate()
    const { data: authStatus, refetch: refetchAuth } = useApi(getAuthStatus, { cacheKey: 'auth-status' })
    const { data: localModels } = useApi(getLocalModels, { cacheKey: 'local-models' })
    const { data: driveInfo } = useApi(getDriveInfo, { cacheKey: 'drive-info' })

    const [step, setStep] = useState(1)

    const [isEnablingSplitBrain, setIsEnablingSplitBrain] = useState(false)
    const [requiresRestart, setRequiresRestart] = useState(false)
    const [splitBrainError, setSplitBrainError] = useState<string | null>(null)

    useEffect(() => {
        const refreshAuth = () => {
            void refetchAuth()
        }

        const handleVisibilityChange = () => {
            if (document.visibilityState === 'visible') {
                refreshAuth()
            }
        }

        window.addEventListener('focus', refreshAuth)
        document.addEventListener('visibilitychange', handleVisibilityChange)
        return () => {
            window.removeEventListener('focus', refreshAuth)
            document.removeEventListener('visibilitychange', handleVisibilityChange)
        }
    }, [refetchAuth])


    const handleConnectGoogle = async () => {
        await launchGoogleAuth()
    }

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

    const isConnected = authStatus?.connected
    const hasLocalModels = localModels?.ollama.detected || localModels?.lm_studio.detected

    // Block setup if drive is exFAT/FAT32 but mode is not split_brain, or if restart is required
    const isDriveConfigSafe = driveInfo
        ? !(driveInfo.is_portable_fs && driveInfo.lancedb_mode !== 'split_brain')
        : true

    const canProceed = (isConnected || hasLocalModels) && isDriveConfigSafe && !requiresRestart

    return (
        <div className="fixed inset-0 bg-background flex flex-col items-center justify-center p-6 z-50 overflow-hidden">

            {/* Background glow effects */}
            <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/20 blur-[120px] rounded-full mix-blend-screen pointer-events-none" />
            <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-accent-blue/20 blur-[120px] rounded-full mix-blend-screen pointer-events-none" />

            <div className="w-full max-w-2xl glass rounded-3xl p-10 animate-fade-in-up border border-primary/10 shadow-2xl relative z-10 flex flex-col">

                {/* Header */}
                <div className="flex flex-col items-center text-center mb-10">
                    <div className="w-16 h-16 bg-gradient-to-br from-primary to-accent-blue rounded-2xl flex items-center justify-center mb-6 shadow-lg shadow-primary/20">
                        <Brain className="w-8 h-8 text-white" />
                    </div>
                    <h1 className="text-3xl font-bold text-text-primary tracking-tight">Welcome to PMA</h1>
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

                        {/* Drive Compatibility Warning */}
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
                                        <p className="text-red-500 text-xs mt-2">{splitBrainError}</p>
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
                        {isDriveConfigSafe && driveInfo?.is_portable_fs && (
                            <div className="p-3 rounded-xl border border-success bg-success/5 flex items-center gap-3">
                                <CheckCircle2 className="w-4 h-4 text-success flex-shrink-0" />
                                <p className="text-sm text-success font-medium">
                                    Split-Brain mode active — portable drive ({driveInfo.fs_type}) is safe.
                                </p>
                            </div>
                        )}

                        <div className={`p-5 rounded-2xl border transition-all duration-300 ${isConnected ? 'border-success bg-success/5' : 'border-primary/10 bg-white/50'}`}>
                            <div className="flex items-center justify-between">
                                <div>
                                    <h3 className="font-bold text-lg flex items-center gap-2">
                                        Google Gemini Account <span className="text-xs ml-2 bg-primary/10 text-primary px-2 py-0.5 rounded-full">Recommended</span>
                                    </h3>
                                    <p className="text-sm text-text-secondary mt-1">Free, instant intelligence with no setup required.</p>
                                </div>
                                {isConnected ? (
                                    <div className="flex items-center gap-2 text-success font-medium">
                                        <CheckCircle2 className="w-5 h-5" /> 
                                        {authStatus?.method === 'env' ? 'Connected via .env' : 'Connected'}
                                    </div>
                                ) : (
                                    <button onClick={handleConnectGoogle} className="glass-button !bg-primary !text-white hover:!bg-primary-h gap-2 text-sm px-5 py-2">
                                        <Key className="w-4 h-4" /> Connect Google
                                    </button>
                                )}
                            </div>
                        </div>

                        <div className="flex items-center gap-4 my-2">
                            <div className="flex-1 h-px bg-primary/10" />
                            <span className="text-xs text-text-secondary font-medium uppercase tracking-wider">or auto-detected locals</span>
                            <div className="flex-1 h-px bg-primary/10" />
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div className={`p-4 rounded-xl border ${localModels?.ollama.detected ? 'border-success bg-success/5' : 'border-primary/10 bg-white/50'}`}>
                                <h4 className="font-semibold mb-1 flex items-center gap-2">🦙 Ollama</h4>
                                {localModels?.ollama.detected ? (
                                    <span className="text-success text-sm flex items-center gap-1"><CheckCircle2 className="w-4 h-4" /> Detected</span>
                                ) : (
                                    <span className="text-text-secondary text-sm">Not detected on port 11434</span>
                                )}
                            </div>
                            <div className={`p-4 rounded-xl border ${localModels?.lm_studio.detected ? 'border-success bg-success/5' : 'border-primary/10 bg-white/50'}`}>
                                <h4 className="font-semibold mb-1 flex items-center gap-2">🖥️ LM Studio</h4>
                                {localModels?.lm_studio.detected ? (
                                    <span className="text-success text-sm flex items-center gap-1"><CheckCircle2 className="w-4 h-4" /> Detected</span>
                                ) : (
                                    <span className="text-text-secondary text-sm">Not detected on port 1234</span>
                                )}
                            </div>
                        </div>

                        <div className="mt-8 flex justify-end">
                            <button
                                onClick={() => setStep(2)}
                                disabled={!canProceed}
                                className={`glass-button !bg-text-primary !text-white hover:opacity-90 gap-2 px-8 py-3 disabled:opacity-50 disabled:cursor-not-allowed transition-all ${
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
                            Your memory assistant is now intelligent. Next, add some folders to your Library to give it memory context.
                        </p>

                        <div className="mt-6 flex gap-4 w-full">
                            <button
                                onClick={() => {
                                    localStorage.setItem('pma_setup_complete', 'true')
                                    navigate('/')
                                }}
                                className="flex-1 glass-button !bg-primary !text-white justify-center py-4 text-lg font-semibold hover:shadow-lg transition-all"
                            >
                                Go to Library
                                <ChevronRight className="w-5 h-5 ml-1" />
                            </button>
                        </div>
                    </div>
                )}

            </div>
        </div>
    )
}
