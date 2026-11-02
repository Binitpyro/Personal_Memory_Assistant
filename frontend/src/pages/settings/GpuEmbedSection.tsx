/**
 * Opt-in GPU embedding. Off by default and never switched on for the user:
 * installing only provisions the engine, and turning it on (or off) changes
 * the index's vector space, so either direction is saved for the next start
 * and then needs Diagnostics > Rebuild embeddings.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../../useApi'
import { getEmbedGpu, installEmbedGpu, cancelEmbedGpuInstall, setEmbedGpu } from '../../api'
import { confirmThen } from './confirmThen'

export function GpuEmbedSection() {
  const [polling, setPolling] = useState(false)
  const { data: gpu, refetch } = useApi(getEmbedGpu, { refetchInterval: polling ? 1500 : 0 })
  const [note, setNote] = useState('')
  const installing = gpu?.install.status === 'running'
  useEffect(() => setPolling(installing), [installing])

  const handleInstall = () => confirmThen(
    'Install GPU embedding?',
    'Downloads the DirectML runtime (about 150 MB) into its own environment, plus the ' +
      'half-precision model (about 67 MB) if it is not already on this machine.',
    'Install',
    async () => {
      setNote('')
      try {
        await installEmbedGpu()
        refetch()
      } catch (e) { setNote(e instanceof Error ? e.message : 'Install failed.') }
    },
  )

  const handleToggle = (on: boolean) => confirmThen(
    on ? 'Use the graphics card for embedding?' : 'Go back to processor embedding?',
    'Takes effect after PMA restarts. Your existing index was built for the other setting, ' +
      'so rebuild embeddings from Diagnostics afterwards or search will warn it is stale.',
    on ? 'Turn on' : 'Turn off',
    async () => {
      try {
        const res = await setEmbedGpu(on)
        if (!res.ok) setNote(`Cannot turn on: ${res.error_code}`)
        else setNote(res.device_saved !== res.device_active ? 'Saved. Restart PMA, then rebuild embeddings.' : 'Saved.')
        refetch()
      } catch (e) { setNote(e instanceof Error ? e.message : 'Could not save.') }
    },
  )

  const saved = gpu?.device_saved === 'gpu'
  const pending = !!gpu && gpu.device_saved !== gpu.device_active

  return (
    <div className="glass p-6 rounded-2xl border border-primary/10">
      <h2 className="font-bold [font-stretch:80%] text-lg text-text-primary m-0">GPU Embedding</h2>
      <p className="text-sm text-text-secondary mt-1 mb-4">
        Indexes documents on your graphics card instead of the processor — measured about
        5× faster on an RTX 5060 laptop GPU, using roughly 1 GB of graphics memory while it
        runs. Searches still run on the processor. Windows only; off unless you turn it on.
      </p>

      {gpu && !gpu.available && <p className="text-xs text-warning mb-3">{gpu.reason}</p>}
      {gpu?.available && !gpu.installed && !gpu.uv_available && (
        <p className="text-xs text-warning mb-3">
          Requires <code className="font-mono">uv</code>, which wasn&apos;t found on this machine.
        </p>
      )}

      {installing && (
        <div className="mb-3">
          <div className="flex justify-between text-xs text-text-secondary mb-1.5">
            <span>{gpu?.install.message}</span>
            <span>{gpu?.install.pct}%</span>
          </div>
          <div className="h-2 bg-surface border border-rule overflow-hidden">
            <div className="h-full bg-primary transition-all duration-300" style={{ width: `${gpu?.install.pct ?? 0}%` }} />
          </div>
        </div>
      )}
      {gpu?.install.status === 'failed' && (
        <p className="text-xs text-danger mb-3">{gpu.install.error_code}: {gpu.install.message}</p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        {gpu?.installed ? (
          <label className="tap-24 flex items-center gap-2 text-sm font-medium text-text-primary cursor-pointer">
            <input
              type="checkbox"
              checked={saved}
              onChange={(e) => handleToggle(e.target.checked)}
              className="w-4 h-4 accent-primary"
            />
            Use the graphics card
          </label>
        ) : (
          <button
            type="button"
            onClick={handleInstall}
            disabled={!gpu?.available || !gpu?.uv_available || installing}
            className="glass-button disabled:opacity-50"
          >
            {installing ? 'Installing…' : 'Install GPU embedding'}
          </button>
        )}
        {installing && (
          <button type="button" onClick={() => cancelEmbedGpuInstall()} className="glass-button text-sm">Cancel</button>
        )}
      </div>

      {pending && (
        <p className="mt-3 text-xs text-warning">
          Restart PMA to switch to the {saved ? 'graphics card' : 'processor'}, then{' '}
          <Link to="/settings/diagnostics" className="underline">rebuild embeddings</Link>.
        </p>
      )}
      {gpu?.worker?.degraded && (
        <p className="mt-3 text-xs text-warning">
          The graphics card is not being used this session, so indexing runs on the processor
          (same results, slower): {gpu.worker.last_error}
        </p>
      )}
      {note && <p className="mt-3 text-xs text-text-secondary">{note}</p>}
    </div>
  )
}
