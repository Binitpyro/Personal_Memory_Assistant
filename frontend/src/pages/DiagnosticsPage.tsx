import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity, AlertTriangle, ArrowLeft, CheckCircle2, Database, Gauge, MinusCircle, ScanText,
} from 'lucide-react'
import { toast } from 'sonner'
import { useApi, invalidateCache } from '../useApi'
import {
  getHealth, getMetrics, getCompactStatus, compactDatabase, getOcrStatus, reembedVectors,
  type HealthResponse, type StageMetrics, type OcrStatus,
} from '../api'
import { CACHE_KEYS } from '../cacheKeys'
import { Button, Panel, ErrorState } from '../components/ui'
import { formatDateTime } from '../utils/format'

/**
 * Everything the backend already computes and previously threw away.
 *
 * Nothing here is new measurement. Subsystem `detail` strings, per-stage
 * latency percentiles, the compact-DB result and the OCR install stamp were all
 * being returned by existing endpoints with no caller — the only place any of it
 * surfaced was a 10px warning triangle in the collapsed sidebar that said
 * "check the backend logs".
 *
 * A Settings sub-route rather than a sixth nav item: the rail is hover-collapsed
 * and five entries deep, and this is a screen you open when something is wrong,
 * not one you live in.
 */

const SUBSYSTEM_LABEL: Record<string, string> = {
  ocr: 'OCR',
  watcher: 'Folder watcher',
  reranker: 'Reranker',
}

function SubsystemRow({ name, info }: Readonly<{
  name: string
  info: { state: 'up' | 'down' | 'disabled' | 'unknown'; detail: string }
}>) {
  // 'disabled' is a configuration choice and 'unknown' means startup was never
  // attempted. Neither is a fault and neither may render as one.
  const tone = {
    up: { cls: 'text-success', Icon: CheckCircle2, word: 'Running' },
    down: { cls: 'text-error', Icon: AlertTriangle, word: 'Not running' },
    disabled: { cls: 'text-text-secondary', Icon: MinusCircle, word: 'Turned off' },
    unknown: { cls: 'text-text-secondary', Icon: MinusCircle, word: 'Not started' },
  }[info.state] ?? { cls: 'text-text-secondary', Icon: MinusCircle, word: info.state }

  return (
    <div className="flex items-start justify-between gap-4 py-2 border-b border-rule last:border-0">
      <span className="font-medium text-text-primary">{SUBSYSTEM_LABEL[name] ?? name}</span>
      <div className="flex flex-col items-end gap-0.5 text-right">
        <span className={`text-sm flex items-center gap-1.5 ${tone.cls}`}>
          <tone.Icon className="w-4 h-4 shrink-0" /> {tone.word}
        </span>
        {/* The reason was recorded all along and never shown. */}
        {info.detail && (
          <span className="text-xs text-text-secondary max-w-md break-words">{info.detail}</span>
        )}
      </div>
    </div>
  )
}

function Card({ icon: Icon, title, blurb, children }: Readonly<{
  icon: typeof Activity
  title: string
  blurb: string
  children: React.ReactNode
}>) {
  return (
    <Panel className="p-6">
      <div className="flex items-start gap-4 mb-5">
        {/* A recess, not a tinted wash: the chip is cut into the panel. */}
        <div className="p-3 bg-raised border border-rule rounded-sm">
          <Icon className="w-5 h-5 text-primary" />
        </div>
        <div>
          <h2 className="font-serif text-lg font-medium text-text-primary">{title}</h2>
          <p className="text-sm text-text-secondary mt-1">{blurb}</p>
        </div>
      </div>
      {children}
    </Panel>
  )
}

const STAGE_LABEL: Record<string, string> = {
  retrieval: 'Retrieval',
  llm_generation: 'Answer generation',
  challenge_retrieval: 'Challenge retrieval',
}

export function DiagnosticsPage() {
  const { data: health } = useApi(getHealth, { cacheKey: CACHE_KEYS.health, refetchInterval: 30_000 })
  const { data: metrics } = useApi(getMetrics, { cacheKey: CACHE_KEYS.metrics, refetchInterval: 30_000 })
  const { data: compact, refetch: refetchCompact } = useApi(getCompactStatus, { cacheKey: CACHE_KEYS.compactStatus })
  const { data: ocr } = useApi(getOcrStatus, { cacheKey: CACHE_KEYS.ocrStatus })
  const [compacting, setCompacting] = useState(false)
  const [reembedding, setReembedding] = useState(false)

  const subsystems = Object.entries((health as HealthResponse | undefined)?.subsystems ?? {})
  const signature = health?.embedding_signature
  const metricRows = Object.entries((metrics ?? {}) as Record<string, StageMetrics>)
  const ocrStatus = ocr as OcrStatus | undefined

  const handleCompact = async () => {
    setCompacting(true)
    try {
      const res = await compactDatabase()
      toast.success(res.message || 'Compaction started.')
      invalidateCache(CACHE_KEYS.compactStatus)
      await refetchCompact()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not start compaction.')
    } finally {
      setCompacting(false)
    }
  }

  const reembedState = signature?.reembed
  const reembedRunning = reembedState === 'running' || reembedding

  const handleReembed = () => {
    // sonner action-toast, not confirm(): SearchPage.tsx documents why - it
    // blocks the event loop and renders a platform dialog that does not match
    // the app.
    const run = async () => {
      setReembedding(true)
      try {
        await reembedVectors()
        toast.success('Rebuilding embeddings. This runs in the background.')
        invalidateCache(CACHE_KEYS.health)
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Could not start the rebuild.')
      } finally {
        setReembedding(false)
      }
    }
    toast('Rebuild every embedding?', {
      description:
        'Your files, text and search history are untouched. Only the vectors are rebuilt, '
        + 'which can take a while on a large library.',
      action: { label: 'Rebuild', onClick: () => void run() },
      cancel: { label: 'Cancel', onClick: () => {} },
    })
  }

  return (
    <div className="flex-1 overflow-y-auto p-8">
      <div className="max-w-3xl mx-auto flex flex-col gap-6">

        <div className="flex items-center gap-3">
          {/* Icon-only, so it needs a name: it had none at all. */}
          <Link
            to="/settings"
            aria-label="Back to Settings"
            className="p-2 rounded-sm hover:bg-surface transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="font-serif text-2xl font-normal text-text-primary">Diagnostics</h1>
            <p className="text-sm text-text-secondary">
              What PMA knows about its own health. Version {health?.version ?? '—'}.
            </p>
          </div>
        </div>

        {signature?.mismatch && (
          // ErrorState already carries role="alert", the mono ERROR label and the
          // oxblood leading rule, so this stops hand-rolling a tinted `bg-error/10`
          // box whose text colour was the same hue as its fill.
          <ErrorState
            title="Your search index was built with a different model"
            body={
              <>
                Stored vectors came from <code className="font-mono">{signature.stored || 'unknown'}</code> but
                the running model is <code className="font-mono">{signature.current || 'unknown'}</code>.
                Semantic search is comparing values from two different vector spaces, so results are
                unreliable until the index is rebuilt.
              </>
            }
            actions={
              <>
                <Button variant="danger" size="sm" onClick={handleReembed} disabled={reembedRunning}>
                  {reembedRunning ? 'Rebuilding…' : 'Rebuild embeddings'}
                </Button>
                {/* One live region rather than two conditional spans: a
                    region has to be in the DOM BEFORE its content changes for
                    the change to be announced, so mounting it along with the
                    message announces nothing. */}
                <span className="text-xs text-text-secondary self-center" role="status">
                  {reembedState === 'error' && 'The last rebuild failed — check the backend logs.'}
                  {reembedState === 'done' && 'Rebuild finished. Reload to refresh this page.'}
                </span>
              </>
            }
          />
        )}

        <Card icon={Activity} title="Subsystems" blurb="Optional components, and why any of them is not running.">
          {subsystems.length === 0
            ? <p className="text-sm text-text-secondary">No subsystem information reported.</p>
            : subsystems.map(([name, info]) => <SubsystemRow key={name} name={name} info={info} />)}
          <div className="mt-4 pt-3 border-t border-rule flex justify-between text-sm">
            <span className="text-text-secondary">Database</span>
            <span className="font-medium">{health?.db ?? '—'}</span>
          </div>
          <div className="flex justify-between text-sm mt-1">
            <span className="text-text-secondary">Indexing</span>
            <span className="font-medium">{health?.indexing ?? '—'}</span>
          </div>
        </Card>

        <Card icon={Gauge} title="Query latency" blurb="Measured on this machine, this session. Milliseconds.">
          {metricRows.length === 0 ? (
            <p className="text-sm text-text-secondary">
              Nothing measured yet — ask a question and these fill in.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-text-secondary text-xs uppercase tracking-wider">
                    <th className="text-left font-semibold pb-2">Stage</th>
                    <th className="text-right font-semibold pb-2">p50</th>
                    <th className="text-right font-semibold pb-2">p95</th>
                    <th className="text-right font-semibold pb-2">p99</th>
                    <th className="text-right font-semibold pb-2">Max</th>
                    <th className="text-right font-semibold pb-2">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {metricRows.map(([stage, m]) => (
                    <tr key={stage} className="border-t border-rule">
                      <td className="py-2 font-medium">{STAGE_LABEL[stage] ?? stage}</td>
                      <td className="py-2 text-right font-mono">{m.p50}</td>
                      <td className="py-2 text-right font-mono">{m.p95}</td>
                      <td className="py-2 text-right font-mono">{m.p99}</td>
                      <td className="py-2 text-right font-mono">{m.max}</td>
                      <td className="py-2 text-right font-mono">{m.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card icon={Database} title="Database maintenance" blurb="Reclaims space and re-optimizes the keyword index.">
          <div className="flex items-center justify-between gap-4">
            <div className="text-sm">
              <div className="text-text-secondary">
                {compact?.is_running ? 'Running now…' : `Last run: ${compact?.last_run ? formatDateTime(compact.last_run) : 'never'}`}
              </div>
              {compact?.error && <div className="text-error mt-1">{compact.error}</div>}
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void handleCompact()}
              disabled={compact?.is_running}
              loading={compacting}
            >
              Compact now
            </Button>
          </div>
        </Card>

        {ocrStatus?.installed && (
          <Card icon={ScanText} title="OCR engine" blurb="Which model and hardware are actually in use.">
            <dl className="grid grid-cols-2 gap-y-2 text-sm">
              <dt className="text-text-secondary">Tier</dt>
              <dd className="font-medium">{ocrStatus.tier}</dd>
              <dt className="text-text-secondary">Model</dt>
              <dd className="font-medium font-mono">{ocrStatus.model_version ?? '—'}</dd>
              <dt className="text-text-secondary">Running on</dt>
              <dd className="font-medium font-mono">{ocrStatus.ep ?? '—'}</dd>
              <dt className="text-text-secondary">Installed</dt>
              <dd className="font-medium">{formatDateTime(ocrStatus.installed_at)}</dd>
              <dt className="text-text-secondary">Pages waiting</dt>
              <dd className="font-medium">{ocrStatus.pages_pending}</dd>
            </dl>
            {ocrStatus.last_error && (
              <p className="mt-3 text-xs text-error break-words">{ocrStatus.last_error}</p>
            )}
          </Card>
        )}

      </div>
    </div>
  )
}
