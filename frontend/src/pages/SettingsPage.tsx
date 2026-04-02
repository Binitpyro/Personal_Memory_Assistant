import { useState, useEffect } from 'react'
import { Settings, Shield, Key, CheckCircle2, AlertCircle, LogOut, Cpu, HardDrive } from 'lucide-react'
import { useApi, invalidateCache } from '../useApi'
import { getAuthStatus, disconnectAuth, getLocalModels, getSystemInfo, getLLMPreferences, setLLMPreferences } from '../api'
import { useSearchParams } from 'react-router-dom'

export function SettingsPage() {
    const [searchParams] = useSearchParams()
    const { data: authStatus, refetch: refetchAuth } = useApi(getAuthStatus, { cacheKey: 'auth-status' })
    const { data: localModels } = useApi(getLocalModels, { cacheKey: 'local-models' })
    const { data: sysInfo } = useApi(getSystemInfo, { cacheKey: 'system-info' })
    const { data: llmPrefs, refetch: refetchPrefs } = useApi(getLLMPreferences, { cacheKey: 'llm-prefs' })

    const [message, setMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
    const [provider, setProvider] = useState<'auto' | 'gemini' | 'ollama' | 'lm_studio'>('auto')
    const [geminiModel, setGeminiModel] = useState('gemini-2.5-flash-lite')
    const [ollamaModel, setOllamaModel] = useState('')
    const [lmStudioModel, setLmStudioModel] = useState('')
    const [savingPrefs, setSavingPrefs] = useState(false)

    useEffect(() => {
        // If we just redirected back from Google OAuth
        if (searchParams.get('auth') === 'success') {
            setMessage({ type: 'ok', text: 'Successfully connected Google Account.' })
        }
    }, [searchParams])

    useEffect(() => {
        if (!llmPrefs) return
        setProvider(llmPrefs.provider || 'auto')
        setGeminiModel(llmPrefs.gemini_model || 'gemini-2.5-flash-lite')
        setOllamaModel(llmPrefs.ollama_model || '')
        setLmStudioModel(llmPrefs.lm_studio_model || '')
    }, [llmPrefs])

    const handleConnectGoogle = () => {
        // Redirect top-level window to the backend OAuth start route
        // The backend will handle the redirect to Google
        window.location.href = 'http://localhost:8000/api/auth/google/start'
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

    const handleSavePrefs = async () => {
        setSavingPrefs(true)
        try {
            await setLLMPreferences({
                provider,
                gemini_model: geminiModel || null,
                ollama_model: ollamaModel || null,
                lm_studio_model: lmStudioModel || null
            })
            invalidateCache('llm-prefs')
            refetchPrefs()
            setMessage({ type: 'ok', text: 'LLM preferences saved.' })
        } catch (e) {
            setMessage({ type: 'err', text: e instanceof Error ? e.message : 'Failed to save preferences' })
        } finally {
            setSavingPrefs(false)
        }
    }

    const isConnected = !!authStatus?.connected

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

            {/* Auth Card */}
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
                                        Connected
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
                        {!isConnected ? (
                            <button
                                onClick={handleConnectGoogle}
                                className="glass-button !bg-primary !text-white hover:!bg-primary-h !py-2 !px-4 gap-2"
                            >
                                <Key className="w-4 h-4" />
                                Connect with Google
                            </button>
                        ) : (
                            <button
                                onClick={handleDisconnect}
                                className="glass-button text-error hover:bg-error/10 !py-2 !px-4 gap-2 border border-error/20"
                            >
                                <LogOut className="w-4 h-4" />
                                Disconnect
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {/* Local Models Card */}
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

            {/* LLM Preferences */}
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
                        onClick={handleSavePrefs}
                        disabled={savingPrefs}
                        className="glass-button !bg-primary !text-white hover:!bg-primary-h !py-2 !px-4"
                    >
                        {savingPrefs ? 'Saving...' : 'Save LLM Preferences'}
                    </button>
                </div>
            </div>

            {/* Storage Stats Card */}
            {sysInfo?.volumes && sysInfo.volumes.length > 0 && (
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
                                            className={`h-full rounded-full transition-all duration-700 ${pct > 90 ? 'bg-error' : pct > 75 ? 'bg-warning' : 'bg-primary'}`}
                                            style={{ width: `${pct}%` }}
                                        />
                                    </div>
                                    <div className="text-right text-[10px] text-text-secondary mt-1">{pct}% used · {v.free_gb.toFixed(1)} GB free</div>
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}
        </div>
    )
}
