import { useState, useCallback, useRef, useEffect } from 'react';
import { toast } from 'sonner';
import { useApi, invalidateCache } from '../useApi';
import { getQueryHistory, clearQueryHistory, getFileTree, subscribeProgress, type HistoryItem } from '../api';

import { useChatStream } from '../hooks/useChatStream';
import { MessageBubble } from '../components/chat/MessageBubble';
import { FramesPanel } from '../components/chat/FramesPanel';
import { Receipt } from '../components/chat/Receipt';
import { FilterBar } from '../components/chat/FilterBar';
import { ModelPicker } from '../components/providers/ModelPicker';
import { useWorking } from '../components/ui';
import { useDreamscapeStore } from '../store/dreamscapeStore';
import { CACHE_KEYS } from '../cacheKeys'

/**
 * Ask, composed as the Safelight board draws it: the ask bar across the top,
 * then three panes — Frames (what the latest answer rests on), Answer (the
 * conversation, question in stock and answer in print) and the Receipt in its
 * printer slot. The window's grain crawls while PMA searches and writes.
 *
 * One deliberate departure: the board shows one answer at a time. The Answer
 * pane keeps the whole conversation, so a follow-up keeps its visible
 * context; Frames and Receipt follow the latest answer.
 */
export function SearchPage() {
  const selectedChunks = useDreamscapeStore(state => state.selectedChunks);
  const removeChunk = useDreamscapeStore(state => state.removeChunk);
  const clearChunks = useDreamscapeStore(state => state.clearChunks);

  const [question, setQuestion] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const [selectedFileType, setSelectedFileType] = useState('');
  const [selectedFolderTag, setSelectedFolderTag] = useState('');
  const [selectedMode, setSelectedMode] = useState('');

  const { data: historyData, refetch: refetchHistory } = useApi(getQueryHistory, { cacheKey: CACHE_KEYS.queryHistory });

  const { messages, executeSearch, resetChat: resetChatStream, stopStream } = useChatStream(() => {
    invalidateCache(CACHE_KEYS.queryHistory);
    refetchHistory();
  });

  const isSearching = messages.at(-1)?.isStreaming ?? false;
  useWorking(isSearching);
  const newestFirst = [...messages].reverse();
  const latestAnswer = newestFirst.find(m => m.role === 'assistant');
  const latestQuestionId = newestFirst.find(m => m.role === 'user')?.id;

  // U-5: the file tree changes only when indexing does, and subscribeProgress
  // already pushes those events. A fixed 15s poll re-fetched the whole tree
  // forever on a corpus that had not moved.
  const { data: fileTree, refetch: refetchFileTree } = useApi(getFileTree, {
    cacheKey: CACHE_KEYS.fileTree,
  });

  useEffect(() => {
    let last = '';
    const unsubscribe = subscribeProgress((evt) => {
      // Refresh on the transition into a settled state, not on every progress
      // frame - those arrive continuously during a scan.
      const status = evt?.status ?? '';
      if (status !== last && (status === 'completed' || status === 'idle')) {
        invalidateCache(CACHE_KEYS.fileTree);
        refetchFileTree();
      }
      last = status;
    });
    return unsubscribe;
  }, [refetchFileTree]);


  const inputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
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

    try {
      await executeSearch(userMsg, {
        file_type: selectedFileType || undefined,
        folder_tag: selectedFolderTag || undefined,
        mode: selectedMode || undefined,
        forced_chunk_id: forcedChunkId,
        selected_chunk_ids: selectedChunks.map(c => c.id),
        isRetry: !!overrideQuestion || !!forcedChunkId
      });
    } catch {
      // A failed stream is stored on its assistant message by useChatStream and
      // rendered inside that bubble; there is no separate banner to feed.
    }
  }, [question, isSearching, executeSearch, selectedFileType, selectedFolderTag, selectedMode, messages, selectedChunks]);

  // Native confirm()/alert() rendered as platform dialogs that do not match the
  // app's visual language, and confirm() blocks the event loop. sonner was
  // already a declared dependency and imported nowhere.
  const handleClearHistory = useCallback(() => {
    const doClear = async () => {
      try {
        const res = await clearQueryHistory();
        invalidateCache(CACHE_KEYS.queryHistory);
        refetchHistory();
        resetChatStream();
        // The backend reports whether the persistent semantic cache went with
        // it. Saying "cleared" when verbatim questions are still on disk is the
        // kind of claim this app cannot afford to get wrong.
        if (res && res.semantic_cache_cleared === false) {
          toast.warning('History cleared, but the saved-answer cache could not be cleared.');
        } else {
          toast.success('Chat history cleared.');
        }
      } catch (e) {
        toast.error(`Failed to clear history: ${e instanceof Error ? e.message : 'Unknown error'}`);
      }
    };

    toast('Clear all chat history?', {
      action: { label: 'Clear', onClick: () => void doClear() },
      cancel: { label: 'Cancel', onClick: () => {} },
    });
  }, [refetchHistory, resetChatStream]);

  const resetChat = () => {
    resetChatStream();
    setQuestion('');
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

  const history = historyData?.history ?? [];
  const paneLink = 'tap-24 hover:text-text-primary underline-offset-4 hover:underline';

  return (
    <div className="sl-askscreen">
      {/* The ask bar: a lamp Q., the question, its scope, the model, and the plate. */}
      <form
        className="sl-ask sl-ask--bar"
        role="search"
        onSubmit={(e) => { e.preventDefault(); void handleSearch(); }}
      >
        <span className="sl-ask__q" aria-hidden="true">Q.</span>
        <input
          ref={inputRef}
          className="sl-ask__input"
          aria-label="Ask your files"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={isSearching ? 'Writing…' : 'Ask a follow-up or a new question...'}
          disabled={isSearching}
          autoComplete="off"
          spellCheck={false}
        />
        <span className="sl-ask__scope">
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
        </span>
        <ModelPicker />
        {/* Visible text leads each accessible name (WCAG 2.5.3). */}
        {isSearching ? (
          <button type="button" className="sl-ask__enter sl-ask__enter--stop" onClick={stopStream} aria-label="Stop generating" title="Stop generating">
            Stop
          </button>
        ) : (
          <button type="submit" className="sl-ask__enter" disabled={!question.trim()} aria-label="Ask — send question">
            <span aria-hidden>⏎</span> Ask
          </button>
        )}
      </form>

      <div className="sl-panes">
        <FramesPanel
          message={latestAnswer}
          onForceInclude={(chunkId) => void handleSearch('', chunkId)}
        />

        <section className="sl-answerpane" aria-label="Answer">
          <div className="sl-panehead">
            <span>Answer</span>
            <span className="relative flex items-center gap-4">
              <button
                type="button"
                className={paneLink}
                onClick={() => setShowHistory(v => !v)}
                aria-haspopup="listbox"
                aria-expanded={showHistory}
              >
                {history.length} recent <span aria-hidden>▾</span>
              </button>
              <button type="button" className={paneLink} onClick={resetChat}>
                <span aria-hidden>↻</span> New chat
              </button>
              {history.length > 0 && (
                <button type="button" className={paneLink} onClick={handleClearHistory}>
                  Clear history
                </button>
              )}
              {showHistory && history.length > 0 && (
                <div
                  className="absolute right-0 top-full mt-2 w-[26rem] bg-raised border border-rule shadow-xl z-20 normal-case tracking-normal"
                  role="listbox"
                  aria-label="Recent questions"
                >
                  <div className="max-h-64 overflow-y-auto custom-scrollbar">
                    {history.slice(0, 10).map((h: HistoryItem) => (
                      <button
                        key={`${h.created_at}-${h.question}`}
                        type="button"
                        role="option"
                        aria-selected="false"
                        className="w-full text-left px-4 py-2.5 font-sans text-sm text-text-primary hover:bg-surface border-b border-rule last:border-none truncate"
                        onClick={() => {
                          setQuestion(h.question);
                          setShowHistory(false);
                          inputRef.current?.focus();
                        }}
                      >
                        {h.question}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </span>
          </div>

          {selectedChunks.length > 0 && (
            <div className="flex flex-col gap-3 px-11 pt-5">
              {selectedChunks.length > 0 && (
                <div className="flex flex-wrap gap-2 items-center">
                  <span className="font-mono text-[10px] uppercase tracking-[.12em] text-text-tertiary">Context</span>
                  {selectedChunks.map(chunk => (
                    <div key={chunk.id} className="flex items-center gap-1.5 px-2.5 py-1 bg-raised border border-rule text-xs text-text-primary">
                      <span className="truncate max-w-[150px]">{chunk.filename}</span>
                      <button
                        type="button"
                        onClick={() => removeChunk(chunk.id)}
                        aria-label={`Remove ${chunk.filename} from context`}
                        className="tap-24 hover:text-error transition-colors"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                  <button type="button" onClick={() => clearChunks()} className={`font-mono text-[10px] uppercase tracking-[.1em] text-text-secondary ${paneLink}`}>
                    Clear
                  </button>
                </div>
              )}
            </div>
          )}

          {/* `log` + polite: an answer streams in token by token, so assertive
              would interrupt continuously. */}
          <div className="flex-1 flex flex-col" role="log" aria-live="polite" aria-label="Conversation">
            {messages.length === 0 ? (
              // The answer column's idle state. Nothing is lit until a question is asked.
              <div className="sl-answer__idle">
                <strong className="sl-stock">Nothing developed yet.</strong>
                <span>Ask, and only the frames that hold the answer come up.</span>
              </div>
            ) : (
              <div className="sl-answer">
                {messages.map((msg) => (
                  <MessageBubble key={msg.id} message={msg} latest={msg.id === latestQuestionId} />
                ))}
                <div ref={messagesEndRef} />
              </div>
            )}
          </div>
        </section>

        <section className="sl-receiptpane" aria-label="Receipt">
          <div className="sl-panehead">
            <span>Receipt</span>
          </div>
          <div className="sl-receiptpane__body">
            {/* Same condition the old metadata row used: the path is known only
                once sources arrive, and a stopped stream never gets one. Keyed on
                the answer, so each new one prints out of the slot afresh. */}
            {latestAnswer && !latestAnswer.isStreaming && latestAnswer.mode ? (
              <Receipt key={latestAnswer.id} msg={latestAnswer} />
            ) : (
              <div className="sl-receipt__slot sl-receipt__slot--idle" aria-hidden="true" />
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
