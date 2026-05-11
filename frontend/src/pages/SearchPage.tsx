import { useState, useCallback, useRef, useEffect } from 'react'
import { Search, Send, Sparkles, Loader2, FileText, Clock, Trash2, User, Bot, RotateCcw, Filter } from 'lucide-react'
import { useApi, invalidateCache } from '../useApi'
import { getQueryHistory, clearQueryHistory, subscribeQuery, getFileTree, getAppConfig, type QuerySource, type QueryStreamChunk } from '../api'

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: QuerySource[]
  latency_ms?: number
  isStreaming?: boolean
  mode?: 'fast_path' | 'full_rag' | 'degraded_rag'
}

export function SearchPage() {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [selectedFileType, setSelectedFileType] = useState('')
  const [selectedFolderTag, setSelectedFolderTag] = useState('')

  // P10-1: SSE Throttling Buffer to prevent render thrashing
  const streamBufferRef = useRef('')
  const lastUpdateRef = useRef(0)
  const throttleTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const { data: historyData, refetch: refetchHistory } = useApi(getQueryHistory, { cacheKey: 'query-history' })
  // P2-0: Disable fileTree polling while query SSE is active (searching=true) to avoid
  // redundant network calls competing with the live stream.
  const { data: fileTree } = useApi(getFileTree, { cacheKey: 'files-tree', refetchInterval: searching ? 0 : 15_000 })
  const { data: appConfig } = useApi(getAppConfig, { cacheKey: 'app-config' })

  const inputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    return () => {
      if (throttleTimeoutRef.current) clearTimeout(throttleTimeoutRef.current)
    }
  }, [])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const flushStreamBuffer = useCallback(() => {
    const text = streamBufferRef.current
    if (!text) return

    setMessages(prev => {
      const last = prev.at(-1)
      if (last?.role === 'assistant') {
        return [
          ...prev.slice(0, -1),
          { ...last, content: text, mode: last.mode || 'full_rag' }
        ]
      }
      return prev
    })
    lastUpdateRef.current = Date.now()
  }, [])

  const handleSearch = useCallback(async () => {
    if (!question.trim() || searching) return

    const userMsg = question.trim()
    setQuestion('')
    setError(null)
    setSearching(true)
    streamBufferRef.current = ''
    lastUpdateRef.current = 0

    // Add user message
    const newMessages: Message[] = [...messages, { role: 'user', content: userMsg }]

    // Add empty assistant message for streaming
    setMessages([...newMessages, { role: 'assistant', content: '', isStreaming: true }])

    const historyForApi = messages.map(m => ({ role: m.role, content: m.content }))

    let sources: QuerySource[] = []
    let latency = 0

    const unsubscribe = subscribeQuery({
      question: userMsg,
      history: historyForApi,
      file_type: selectedFileType || null,
      folder_tag: selectedFolderTag || null
    }, (chunk: QueryStreamChunk) => {
      if (chunk.type === 'error') {
        setError(chunk.text || 'Search failed')
        setSearching(false)
        setMessages(newMessages)
        return
      }

      if (chunk.type === 'sources') {
        sources = chunk.sources || []
        latency = chunk.latency_ms || chunk.retrieval_ms || 0
        setMessages(prev => {
          const last = prev.at(-1)
          if (last?.role === 'assistant') {
            return [...prev.slice(0, -1), { ...last, sources, latency_ms: latency, mode: chunk.mode as any || 'full_rag' }]
          }
          return prev
        })
      }

      if (chunk.type === 'fast_path') {
        const fullText = chunk.answer || chunk.text || ''
        setMessages(prev => {
          const last = prev.at(-1)
          if (last?.role === 'assistant') {
            return [...prev.slice(0, -1), { ...last, content: fullText, sources: chunk.sources || sources, latency_ms: chunk.latency_ms || latency, mode: 'fast_path' }]
          }
          return prev
        })
      }

      if (chunk.type === 'ping') {
        // Ignore keep-alive pings silently
        return
      }

      if (chunk.type === 'content' && chunk.text) {
        streamBufferRef.current += chunk.text
        
        // Throttle updates to ~50ms
        const now = Date.now()
        if (now - lastUpdateRef.current > 50) {
          flushStreamBuffer()
        } else if (!throttleTimeoutRef.current) {
          throttleTimeoutRef.current = setTimeout(() => {
            throttleTimeoutRef.current = null
            flushStreamBuffer()
          }, 50)
        }
      }

      if (chunk.type === 'done') {
        if (throttleTimeoutRef.current) {
          clearTimeout(throttleTimeoutRef.current)
          throttleTimeoutRef.current = null
        }
        flushStreamBuffer()
        setSearching(false)
        setMessages(prev => {
          const last = prev.at(-1)
          if (last) return [...prev.slice(0, -1), { ...last, isStreaming: false }]
          return prev
        })
        invalidateCache('query-history')
        refetchHistory()
      }

    })

    return unsubscribe
  }, [question, searching, messages, refetchHistory, selectedFileType, selectedFolderTag, flushStreamBuffer])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSearch()
    }
  }

  const handleClearHistory = useCallback(async () => {
    if (!confirm('Are you sure you want to clear all chat history?')) return
    try {
      await clearQueryHistory()
      invalidateCache('query-history')
      refetchHistory()
      setMessages([])
    } catch (e) {
      alert(`Failed to clear history: ${e instanceof Error ? e.message : 'Unknown error'}`)
    }
  }, [refetchHistory])

  const resetChat = () => {
    setMessages([])
    setQuestion('')
    setError(null)
  }

  const folderOptions = Object.keys(fileTree?.folders ?? {}).sort((a, b) => a.localeCompare(b))
  const fileTypeOptions = Array.from(
    new Set(
      Object.values(fileTree?.folders ?? {})
        .flat()
        .map(entry => entry.type)
        .filter(Boolean)
    )
  ).sort((a, b) => a.localeCompare(b))

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden animate-fade-in-up">
      {/* Header */}
      <div className="flex items-center justify-between p-6 shrink-0">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3 text-text-primary">
            <Search className="w-7 h-7 text-primary" />
            AI Chat
          </h1>
          <p className="text-text-secondary mt-1 text-sm">
            Conversational memory assistant
          </p>
        </div>
        <button
          onClick={resetChat}
          className="flex items-center gap-2 px-4 py-2 bg-white/5 hover:bg-white/10 rounded-xl text-xs font-bold transition-all text-text-secondary border border-white/5 shadow-sm"
        >
          <RotateCcw className="w-3.5 h-3.5" /> NEW CHAT
        </button>
      </div>

      {/* Chat Area */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center opacity-40">
            <Bot className="w-16 h-16 text-primary mb-4" />
            <h2 className="text-xl font-bold text-white mb-2">How can I help you?</h2>
            <p className="max-w-sm text-sm">Ask about your documents, codebases, or project statistics. I remember our conversation context.</p>
          </div>
        ) : (
          <div className="max-w-4xl mx-auto space-y-8">
            {messages.map((msg, idx) => (
              <div key={`${msg.role}-${idx}-${msg.content.substring(0, 20)}`} className={`flex gap-4 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                {msg.role === 'assistant' && (
                  <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center shrink-0 border border-primary/30 shadow-lg">
                    <Bot className="w-4 h-4 text-primary-light" />
                  </div>
                )}
                <div className={`flex flex-col gap-2 max-w-[85%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                  <div className={`px-5 py-3 rounded-2xl text-sm leading-relaxed shadow-sm border ${msg.role === 'user'
                    ? 'bg-primary text-white border-primary-light/20 rounded-tr-none'
                    : 'glass-card !p-3 text-text-primary border-white/80 rounded-tl-none'
                    }`}>
                    {msg.isStreaming && !msg.content ? (
                      <div className="flex gap-1 py-1">
                        <span className="w-1.5 h-1.5 bg-primary-light rounded-full animate-bounce"></span>
                        <span className="w-1.5 h-1.5 bg-primary-light rounded-full animate-bounce [animation-delay:0.2s]"></span>
                        <span className="w-1.5 h-1.5 bg-primary-light rounded-full animate-bounce [animation-delay:0.4s]"></span>
                      </div>
                    ) : (
                      <div className="whitespace-pre-wrap">{msg.content}</div>
                    )}
                  </div>

                  {/* Mode Badge */}
                  {msg.role === 'assistant' && !msg.isStreaming && msg.mode && (
                    <div className="flex items-center gap-2 mt-1">
                      <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                        msg.mode === 'fast_path'
                          ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                          : msg.mode === 'degraded_rag'
                            ? 'bg-orange-500/10 text-orange-400 border-orange-500/20'
                            : 'bg-primary/10 text-primary-light border-primary/20'
                        }`}>
                        {msg.mode === 'fast_path' ? '⚡ Fast Answer' : msg.mode === 'degraded_rag' ? '⚠️ Degraded RAG' : '🔍 RAG Answer'}
                      </span>
                      {msg.latency_ms != null && msg.latency_ms > 0 && (
                        <span className="text-[10px] text-text-secondary/50">{msg.latency_ms.toFixed(0)}ms</span>
                      )}
                    </div>
                  )}

                  {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-1">
                      {msg.sources.slice(0, 3).map((src) => (
                        <div key={`${src.file_path}-${src.score || 0}`} className="flex items-center gap-1.5 px-2 py-1 bg-white/5 rounded-lg text-[10px] text-text-secondary border border-white/5">
                          <FileText className="w-3 h-3 text-primary-light" />
                          <span className="max-w-[150px] truncate">{src.file_path.split(/[\\/]/).pop()}</span>
                        </div>
                      ))}
                      {msg.sources.length > 3 && (
                        <span className="text-[10px] text-text-secondary self-center">+{msg.sources.length - 3} more</span>
                      )}
                    </div>
                  )}
                </div>
                {msg.role === 'user' && (
                  <div className="w-8 h-8 rounded-full bg-surface-lighter flex items-center justify-center shrink-0 border border-white/10 shadow-lg">
                    <User className="w-4 h-4 text-text-secondary" />
                  </div>
                )}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Input Area */}
      <div className="p-6 shrink-0 bg-surface-dark/50 backdrop-blur-md border-t border-white/5">
        <div className="max-w-4xl mx-auto flex flex-col gap-3">
          {error && (
            <div className="bg-error/10 border border-error/20 text-error text-xs p-3 rounded-xl flex items-center justify-between">
              <span>{error}</span>
              <button onClick={() => setError(null)} className="font-bold opacity-60 hover:opacity-100">&times;</button>
            </div>
          )}
          {/* Recent searches dropdown */}
          {showHistory && historyData?.history && historyData.history.length > 0 && (
            <div className="absolute bottom-full mb-2 left-0 right-0 glass rounded-2xl border border-primary/10 shadow-2xl overflow-hidden z-20">
              <div className="px-4 py-2 text-[10px] font-black text-text-secondary border-b border-white/5 uppercase tracking-widest">Recent Searches</div>
              <div className="max-h-48 overflow-y-auto custom-scrollbar">
                {historyData.history.slice(0, 10).map((h: any) => (
                  <button
                    key={`${h.created_at}-${h.question}`}
                    className="w-full text-left px-4 py-2.5 text-sm text-text-primary hover:bg-primary/10 transition-colors flex items-center gap-3 border-b border-white/5 last:border-none"
                    onClick={() => {
                      setQuestion(h.question)
                      setShowHistory(false)
                      inputRef.current?.focus()
                    }}
                  >
                    <Clock className="w-3.5 h-3.5 text-text-secondary shrink-0" />
                    <span className="truncate">{h.question}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="relative group">
            <div className="absolute -inset-0.5 bg-gradient-to-r from-primary to-accent rounded-2xl blur opacity-20 group-focus-within:opacity-40 transition duration-1000"></div>
            <div className="relative flex items-center glass rounded-2xl overflow-hidden shadow-2xl">
              <input
                ref={inputRef}
                type="text"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={searching ? "AI is thinking..." : "Ask a follow-up or a new question..."}
                className="flex-1 bg-transparent px-6 py-4 text-text-primary placeholder:text-text-secondary/50 focus:outline-none text-base"
                disabled={searching}
              />
              <button
                onClick={handleSearch}
                disabled={!question.trim() || searching}
                className="p-3 mr-2 bg-primary hover:bg-primary-dark disabled:bg-white/5 text-white rounded-xl transition-all shadow-lg"
              >
                {searching ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
              </button>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 px-1">
            <span className="text-[10px] text-text-secondary font-bold uppercase tracking-widest flex items-center gap-1">
              <Filter className="w-3 h-3" /> Quick Filters
            </span>
            <select
              value={selectedFileType}
              onChange={(e) => setSelectedFileType(e.target.value)}
              className="text-[11px] bg-white/5 border border-white/10 rounded-lg px-2 py-1 text-text-primary"
              disabled={searching}
            >
              <option value="">All file types</option>
              {fileTypeOptions.map((ext) => (
                <option key={ext} value={ext}>{ext}</option>
              ))}
            </select>
            <select
              value={selectedFolderTag}
              onChange={(e) => setSelectedFolderTag(e.target.value)}
              className="text-[11px] bg-white/5 border border-white/10 rounded-lg px-2 py-1 text-text-primary"
              disabled={searching}
            >
              <option value="">All folders</option>
              {folderOptions.map((folder) => (
                <option key={folder} value={folder}>{folder}</option>
              ))}
            </select>
            {(selectedFileType || selectedFolderTag) && (
              <button
                type="button"
                onClick={() => {
                  setSelectedFileType('')
                  setSelectedFolderTag('')
                }}
                className="text-[10px] px-2 py-1 rounded-lg border border-primary/20 text-primary-light hover:bg-primary/10"
              >
                Clear filters
              </button>
            )}
          </div>
          <div className="flex items-center justify-between px-2">
            <div className="flex gap-4 text-[10px] text-text-secondary font-bold uppercase tracking-widest">
              <span className="flex items-center gap-1"><Sparkles className="w-3 h-3 text-primary" /> {appConfig?.gemini_model ?? 'AI Model'}</span>
              <button
                onClick={() => setShowHistory(v => !v)}
                className={`flex items-center gap-1 hover:text-text-primary transition-colors ${showHistory ? 'text-primary' : ''}`}
              >
                <Clock className="w-3 h-3" /> {historyData?.history?.length ?? 0} Recent
              </button>
            </div>
            {historyData?.history && historyData.history.length > 0 && (
              <button
                onClick={handleClearHistory}
                className="text-[10px] font-black text-error/60 hover:text-error transition-colors flex items-center gap-1"
              >
                <Trash2 className="w-3 h-3" /> CLEAR HISTORY
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
