import { useState, useCallback, useRef, useEffect } from 'react';
import { Search, Send, Loader2, Sparkles, Clock, Trash2, RotateCcw } from 'lucide-react';
import { useApi, invalidateCache } from '../useApi';
import { getQueryHistory, clearQueryHistory, getFileTree, getAppConfig } from '../api';
import { useChatStream } from '../hooks/useChatStream';
import { MessageBubble } from '../components/chat/MessageBubble';
import { FilterBar } from '../components/chat/FilterBar';

export function SearchPage() {
  const [question, setQuestion] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [selectedFileType, setSelectedFileType] = useState('');
  const [selectedFolderTag, setSelectedFolderTag] = useState('');
  const [selectedMode, setSelectedMode] = useState('');

  const { data: historyData, refetch: refetchHistory } = useApi(getQueryHistory, { cacheKey: 'query-history' });
  
  const { messages, executeSearch, resetChat: resetChatStream } = useChatStream(() => {
    invalidateCache('query-history');
    refetchHistory();
  });

  const isSearching = messages.at(-1)?.isStreaming ?? false;

  const { data: fileTree } = useApi(getFileTree, { cacheKey: 'files-tree', refetchInterval: isSearching ? 0 : 15_000 });
  const { data: appConfig } = useApi(getAppConfig, { cacheKey: 'app-config' });

  const inputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSearch = useCallback(async (overrideQuestion?: string, forcedChunkId?: number) => {
    let userMsg = overrideQuestion || question.trim();
    
    // If we're forcing a chunk but have no question text, re-use the last user question
    if (!userMsg && forcedChunkId) {
      const lastUserMsg = messages.filter(m => m.role === 'user').pop();
      if (lastUserMsg) {
        userMsg = lastUserMsg.content;
      }
    }

    if (!userMsg || isSearching) return;

    if (!overrideQuestion) setQuestion('');
    setError(null);

    try {
      await executeSearch(userMsg, {
        file_type: selectedFileType || undefined,
        folder_tag: selectedFolderTag || undefined,
        mode: selectedMode || undefined,
        forced_chunk_id: forcedChunkId,
        isRetry: !!overrideQuestion || !!forcedChunkId
      });
    } catch (err: any) {
      setError(err.message || 'Search failed');
    }
  }, [question, isSearching, executeSearch, selectedFileType, selectedFolderTag, selectedMode, messages]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSearch();
    }
  };

  const handleClearHistory = useCallback(async () => {
    if (!confirm('Are you sure you want to clear all chat history?')) return;
    try {
      await clearQueryHistory();
      invalidateCache('query-history');
      refetchHistory();
      resetChatStream();
    } catch (e) {
      alert(`Failed to clear history: ${e instanceof Error ? e.message : 'Unknown error'}`);
    }
  }, [refetchHistory, resetChatStream]);

  const resetChat = () => {
    resetChatStream();
    setQuestion('');
    setError(null);
  };

  const folderOptions = Object.keys(fileTree?.folders ?? {}).sort((a, b) => a.localeCompare(b));
  const fileTypeOptions = Array.from(
    new Set(
      Object.values(fileTree?.folders ?? {})
        .flat()
        .map(entry => entry.type)
        .filter(Boolean)
    )
  ).sort((a, b) => a.localeCompare(b));

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
            <Sparkles className="w-16 h-16 text-primary mb-4" />
            <h2 className="text-xl font-bold text-white mb-2">How can I help you?</h2>
            <p className="max-w-sm text-sm">Ask about your documents, codebases, or project statistics. I remember our conversation context.</p>
          </div>
        ) : (
          <div className="max-w-4xl mx-auto space-y-8">
            {messages.map((msg) => (
              <MessageBubble 
                key={msg.id} 
                message={msg} 
                onNearMissClick={(suggestion: string, forcedChunkId?: number) => handleSearch(suggestion, forcedChunkId)} 
              />
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
            <div 
              className="absolute bottom-full mb-2 left-0 right-0 glass rounded-2xl border border-primary/10 shadow-2xl overflow-hidden z-20"
              role="listbox"
            >
              <div className="px-4 py-2 text-[10px] font-black text-text-secondary border-b border-white/5 uppercase tracking-widest">Recent Searches</div>
              <div className="max-h-48 overflow-y-auto custom-scrollbar">
                {historyData.history.slice(0, 10).map((h: any) => (
                  <button
                    key={`${h.created_at}-${h.question}`}
                    role="option"
                    aria-selected="false"
                    className="w-full text-left px-4 py-2.5 text-sm text-text-primary hover:bg-primary/10 transition-colors flex items-center gap-3 border-b border-white/5 last:border-none"
                    onClick={() => {
                      setQuestion(h.question);
                      setShowHistory(false);
                      inputRef.current?.focus();
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
                placeholder={isSearching ? "AI is thinking..." : "Ask a follow-up or a new question..."}
                className="flex-1 bg-transparent px-6 py-4 text-text-primary placeholder:text-text-secondary/50 focus:outline-none text-base"
                disabled={isSearching}
                aria-expanded={showHistory}
              />
              <button
                onClick={() => handleSearch()}
                disabled={!question.trim() || isSearching}
                className="p-3 mr-2 bg-primary hover:bg-primary-dark disabled:bg-white/5 text-white rounded-xl transition-all shadow-lg"
              >
                {isSearching ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
              </button>
            </div>
          </div>
          
          <FilterBar 
            selectedFileType={selectedFileType}
            setSelectedFileType={setSelectedFileType}
            selectedFolderTag={selectedFolderTag}
            setSelectedFolderTag={setSelectedFolderTag}
            selectedMode={selectedMode}
            setSelectedMode={setSelectedMode}
            fileTypeOptions={fileTypeOptions}
            folderOptions={folderOptions}
            disabled={isSearching}
          />
          
          <div className="flex items-center justify-between px-2">
            <div className="flex gap-4 text-[10px] text-text-secondary font-bold uppercase tracking-widest">
              <span className="flex items-center gap-1"><Sparkles className="w-3 h-3 text-primary" /> {appConfig?.gemini_model ?? 'AI Model'}</span>
              <button
                onClick={() => setShowHistory(v => !v)}
                className={`flex items-center gap-1 hover:text-text-primary transition-colors ${showHistory ? 'text-primary' : ''}`}
                aria-haspopup="listbox"
                aria-expanded={showHistory}
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
  );
}
