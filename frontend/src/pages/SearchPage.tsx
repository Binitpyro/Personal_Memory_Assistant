import { useState, useCallback, useRef, useEffect } from 'react'
import { Search, Send, Sparkles, Loader2, FileText, Clock, ChevronDown, ChevronUp } from 'lucide-react'
import { useApi } from '../useApi'
import { postQuery, getQueryHistory, type QueryResponse, type QuerySource } from '../api'

export function SearchPage() {
  const [question, setQuestion] = useState('')
  const [searching, setSearching] = useState(false)
  const [result, setResult] = useState<QueryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showSources, setShowSources] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const { data: historyData, refetch: refetchHistory } = useApi(
    () => getQueryHistory(10),
    { cacheKey: 'query-history' },
  )

  // Debounce timer ref for potential future auto-search
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const handleSearch = useCallback(async () => {
    const q = question.trim()
    if (!q || searching) return
    clearTimeout(debounceRef.current)
    setSearching(true)
    setError(null)
    setResult(null)
    try {
      const res = await postQuery(q)
      setResult(res)
      setShowSources(false)
      refetchHistory()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed')
    } finally {
      setSearching(false)
    }
  }, [question, searching, refetchHistory])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSearch()
      }
    },
    [handleSearch],
  )

  const handleHistoryClick = useCallback((q: string) => {
    setQuestion(q)
    inputRef.current?.focus()
  }, [])

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6 animate-fade-in-up custom-scrollbar">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-3">
          <Search className="w-7 h-7 text-primary" />
          Search
        </h1>
        <p className="text-text-secondary mt-1">
          Ask questions about your personal files using AI
        </p>
      </div>

      {/* Search Input */}
      <div className="glass-card">
        <div className="flex gap-3">
          <div className="relative flex-1">
            <Sparkles className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-primary" />
            <input
              ref={inputRef}
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask anything about your files..."
              className="w-full bg-surface-lighter border border-primary/20 rounded-xl pl-12 pr-4 py-4 text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-primary/40 text-lg"
              disabled={searching}
            />
          </div>
          <button
            onClick={handleSearch}
            disabled={!question.trim() || searching}
            className="btn bg-primary hover:bg-primary-dark text-white rounded-xl px-6 disabled:opacity-40"
          >
            {searching ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="glass-card bg-error/10 border-error/30 text-error text-sm">{error}</div>
      )}

      {/* Results */}
      {result && (
        <div className="glass-card space-y-4">
          {/* Answer */}
          <div className="prose prose-invert max-w-none text-text-primary whitespace-pre-wrap leading-relaxed">
            {result.answer}
          </div>

          {/* Timing badge */}
          <div className="flex items-center gap-4 text-xs text-text-secondary">
            <span>{result.latency_ms}ms</span>
            <span>{result.retrieved_count} source(s)</span>
            {result.mode && <span className="bg-primary/20 text-primary-light px-2 py-0.5 rounded">{result.mode}</span>}
          </div>

          {/* Sources toggle */}
          {result.sources.length > 0 && (
            <div>
              <button
                onClick={() => setShowSources(!showSources)}
                className="flex items-center gap-1 text-sm text-primary-light hover:underline"
              >
                {showSources ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                {showSources ? 'Hide' : 'Show'} Sources
              </button>
              {showSources && (
                <div className="mt-3 space-y-2">
                  {result.sources.map((src: QuerySource, i: number) => (
                    <div key={i} className="bg-surface-lighter rounded-xl px-4 py-3 text-sm">
                      <div className="flex items-center gap-2 text-primary-light font-medium mb-1">
                        <FileText className="w-4 h-4" />
                        {src.file_path.split(/[\\/]/).pop()}
                      </div>
                      {src.text && (
                        <p className="text-text-secondary text-xs line-clamp-3">{src.text}</p>
                      )}
                      {src.score !== undefined && (
                        <span className="text-xs text-text-secondary mt-1 block">Score: {src.score.toFixed(2)}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Empty state */}
      {!result && !searching && !error && (
        <div className="glass-card text-center py-16">
          <Search className="w-12 h-12 text-primary/30 mx-auto mb-4" />
          <h3 className="text-lg text-text-secondary">
            Your search results will appear here
          </h3>
          <p className="text-text-secondary/60 text-sm mt-2">
            Try asking: &quot;What did I write about machine learning?&quot;
          </p>
        </div>
      )}

      {/* Loading state */}
      {searching && (
        <div className="glass-card text-center py-16">
          <Loader2 className="w-12 h-12 text-primary mx-auto mb-4 animate-spin" />
          <h3 className="text-lg text-text-secondary">Searching your memory…</h3>
        </div>
      )}

      {/* Query History */}
      <div className="glass-card">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Clock className="w-5 h-5 text-primary" />
          Recent Queries
        </h2>
        {historyData?.history && historyData.history.length > 0 ? (
          <div className="space-y-2">
            {historyData.history.map((item, i) => (
              <button
                key={i}
                onClick={() => handleHistoryClick(item.question)}
                className="w-full text-left bg-surface-lighter rounded-xl px-4 py-3 hover:bg-surface-light transition-colors text-sm"
              >
                <span className="text-text-primary">{item.question}</span>
                {item.latency_ms !== undefined && (
                  <span className="text-text-secondary ml-2 text-xs">({item.latency_ms}ms)</span>
                )}
              </button>
            ))}
          </div>
        ) : (
          <div className="text-text-secondary text-sm">
            No recent queries yet. Start searching to build your history.
          </div>
        )}
      </div>
    </div>
  )
}
