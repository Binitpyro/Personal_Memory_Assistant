/**
 * Extracted from SettingsPage.tsx, which had grown to 1341 lines holding ten
 * unrelated section components in one unbroken scroll. Behaviour is unchanged;
 * only the file boundary moved.
 */
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Cpu } from 'lucide-react'
import { useApi } from '../../useApi'
import { getLLMPreferences, getProviders, type ProviderStatus, type LLMPreferences } from '../../api'
import { CACHE_KEYS } from '../../cacheKeys'

export function LLMPreferencesSection({
  onSave,
  saving
}: Readonly<{
  onSave: (prefs: LLMPreferences) => void
  saving: boolean
}>) {
  const navigate = useNavigate()
  const { data: llmPrefs } = useApi(getLLMPreferences, { cacheKey: CACHE_KEYS.llmPreferences })
  const { data: providers } = useApi(getProviders, { cacheKey: CACHE_KEYS.providersList })
  
  const [provider, setProvider] = useState<string>('auto')
  const [selectedModels, setSelectedModels] = useState<Record<string, string>>({})

  useEffect(() => {
    if (llmPrefs) {
      setProvider(llmPrefs.provider || 'auto')
      const initialModels: Record<string, string> = {}
      if (llmPrefs.gemini_model) initialModels.gemini = llmPrefs.gemini_model
      if (llmPrefs.ollama_model) initialModels.ollama = llmPrefs.ollama_model
      if (llmPrefs.lm_studio_model) initialModels.lm_studio = llmPrefs.lm_studio_model
      setSelectedModels(initialModels)
    }
  }, [llmPrefs])

  const activeProviders = (providers || []).filter(
    (p: ProviderStatus) => p.is_set || p.spec.id === 'ollama' || p.spec.id === 'lm_studio'
  )
  
  const activeProviderSpec = activeProviders.find(p => p.spec.id === provider)
  
  const availableModels = activeProviderSpec 
    ? (activeProviderSpec.last_validation?.models?.map(m => m.id) || [])
    : []

  const currentModel = provider !== 'auto' ? (selectedModels[provider] || '') : ''

  const handleProviderChange = (newProvider: string) => {
    setProvider(newProvider)
    if (newProvider !== 'auto') {
      const pSpec = activeProviders.find(p => p.spec.id === newProvider)
      const pModels = pSpec?.last_validation?.models?.map(m => m.id) || []
      if (pModels.length > 0 && !selectedModels[newProvider]) {
        setSelectedModels(prev => ({ ...prev, [newProvider]: pModels[0] }))
      }
    }
  }

  const handleModelChange = (newModel: string) => {
    if (provider !== 'auto') {
      setSelectedModels(prev => ({ ...prev, [provider]: newModel }))
    }
  }

  const submitSave = () => {
    if (!llmPrefs) return
    const newPrefs: LLMPreferences = { ...llmPrefs, provider }
    Object.entries(selectedModels).forEach(([pId, mId]) => {
      newPrefs[`${pId}_model`] = mId
    })
    onSave(newPrefs)
  }

  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-primary/10 rounded-xl">
          <Cpu className="w-6 h-6 text-primary" />
        </div>
        <div>
          <h2 className="font-serif text-lg font-medium text-text-primary">Model Selection</h2>
          <p className="text-sm text-text-secondary mt-1">
            Choose your preferred intelligence provider and model. For detailed API key configurations, use the advanced view.
          </p>
        </div>
      </div>

      <div className="flex flex-col md:flex-row gap-4 items-end">
        <label className="text-sm text-text-secondary flex flex-col gap-1 w-full md:w-64">
          Provider
          <select 
            value={provider} 
            onChange={(e) => handleProviderChange(e.target.value)} 
            className="bg-raised border border-rule rounded-lg px-3 py-2 text-text-primary"
          >
            <option value="auto">Auto (recommended)</option>
            {activeProviders.map(p => (
              <option key={p.spec.id} value={p.spec.id}>{p.spec.display_name}</option>
            ))}
          </select>
        </label>

        {provider !== 'auto' && availableModels.length > 0 && (
          <label className="text-sm text-text-secondary flex flex-col gap-1 w-full md:w-64 animate-fade-in">
            Model
            <select 
              value={currentModel} 
              onChange={(e) => handleModelChange(e.target.value)} 
              className="bg-raised border border-rule rounded-lg px-3 py-2 text-text-primary"
            >
              <option value="" disabled>Select a model...</option>
              {availableModels.map(mId => (
                <option key={mId} value={mId}>{mId}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="mt-6 flex items-center gap-3">
        <button
          onClick={submitSave}
          disabled={saving || !llmPrefs}
          className="glass-button !bg-plate !text-on-plate hover:!bg-plate !py-2 !px-4"
        >
          {saving ? 'Saving...' : 'Save Preference'}
        </button>
        <button
          onClick={() => navigate('/settings/providers')}
          className="glass-button !bg-primary/10 border border-primary/20 text-primary hover:!bg-primary/20 !py-2 !px-4"
        >
          Manage Providers (Advanced)
        </button>
      </div>
    </div>
  )
}
