import { useState, useCallback, useRef, useEffect } from 'react'
import { Search, Send, Sparkles, Loader2, FileText, Clock, Trash2, User, Bot, RotateCcw } from 'lucide-react'
import { useApi, invalidateCache } from '../useApi'
import { getQueryHistory, clearQueryHistory, subscribeQuery, type QuerySource, type QueryStreamChunk } from '../api'

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: QuerySource[]
  latency_ms?: number
  isStreaming?: boolean
}

export function SearchPage() {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: historyData, refetch: refetchHistory } = useApi(getQueryHistory, { cacheKey: 'query-history' })

  const inputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const handleSearch = useCallback(async () => {
    if (!question.trim() || searching) return

    const userMsg = question.trim()
    setQuestion('')
    setError(null)
    setSearching(true)

    // Add user message
    const newMessages: Message[] = [...messages, { role: 'user', content: userMsg }]

    // Add empty assistant message for streaming
    setMessages([...newMessages, { role: 'assistant', content: '', isStreaming: true }])

    const historyForApi = messages.map(m => ({ role: m.role, content: m.content }))

    let fullText = ''
    let sources: QuerySource[] = []
    let latency = 0

    const unsubscribe = subscribeQuery(userMsg, (chunk: QueryStreamChunk) => {
      if (chunk.type === 'error') {
        setError(chunk.text || 'Search failed')
        setSearching(false)
        // Remove the empty assistant message on error
        setMessages(newMessages)
        return
      }

      if (chunk.type === 'sources') {
        sources = chunk.sources || []
        latency = chunk.latency_ms || chunk.retrieval_ms || 0
      }

      if (chunk.type === 'content' && chunk.text) {
        fullText += chunk.text
        setMessages(prev => {
          const last = prev.at(-1)
          if (last?.role === 'assistant') {
            return [
              ...prev.slice(0, -1),
              { ...last, content: fullText, sources, latency_ms: latency }
            ]
          }
          return prev
        })
      }

      // If the backend indicates it's finished (using a custom end chunk or just stopping)
      // Since SSE reader loop handles 'done', we just wait for the search to stop
    }, { history: historyForApi })

    // We don't have a reliable "done" event in the current subscribeQuery simple implementation
    // So we'll wrap it or just set searching to false after a delay or based on common markers
    // For now, let's assume it finishes when fullText stops updating or add a timeout
    setTimeout(() => {
      setSearching(false)
      setMessages(prev => {
        const last = prev.at(-1)
        if (last) return [...prev.slice(0, -1), { ...last, isStreaming: false }]
        return prev
      })
      invalidateCache('query-history')
      refetchHistory()
    }, 15000) // 15s max for now, proper implementation would yield a 'done' chunk

    return unsubscribe
  }, [question, searching, messages, refetchHistory])

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
              <div key={`msg-${idx}-${msg.content.substring(0, 10)}`} className={`flex gap-4 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
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

                  {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-1">
                      {msg.sources.slice(0, 3).map((src, sidx) => (
                        <div key={`src-${sidx}-${src.file_path}`} className="flex items-center gap-1.5 px-2 py-1 bg-white/5 rounded-lg text-[10px] text-text-secondary border border-white/5">
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
              <button onClick={() => setError(null)} className="font-bold opacity-60 hover:opacity-100">✕</button>
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
          <div className="flex items-center justify-between px-2">
            <div className="flex gap-4 text-[10px] text-text-secondary font-bold uppercase tracking-widest">
              <span className="flex items-center gap-1"><Sparkles className="w-3 h-3 text-primary" /> Gemini 2.5 Flash Lite</span>
              <span className="flex items-center gap-1"><Clock className="w-3 h-3" /> Context Enabled</span>
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
