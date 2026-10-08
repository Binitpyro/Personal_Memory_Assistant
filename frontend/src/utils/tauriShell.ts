export const isTauri = typeof globalThis !== 'undefined' && '__TAURI_INTERNALS__' in globalThis;

export async function initTauriConnection(setEndpoint: (e: string) => void, setToken: (t: string) => void) {
  if (isTauri) {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const [port, token] = await invoke<[number, string]>('get_backend_info');
      setEndpoint(`http://127.0.0.1:${port}`);
      setToken(token);
      sessionStorage.setItem('pma_token', token);
      console.log(`[Tauri] Connected to backend on port ${port}`);
    } catch (e) {
      console.error("[Tauri] Failed to get backend info from shell:", e);
    }
    await surfaceBackendFailures();
  }
}

/**
 * The shell spawns the backend off the UI thread, so a missing sidecar or a
 * crash otherwise leaves a normal-looking window whose requests all fail.
 * Rust stores the last failure (asked for here, in case it happened before we
 * listened) and emits `backend-error` for later ones. Shown once.
 */
async function surfaceBackendFailures() {
  let shown = false;
  const show = async (msg: string) => {
    if (shown) return;
    shown = true;
    console.error('[Tauri] Backend failure:', msg);
    try {
      const { message } = await import('@tauri-apps/plugin-dialog');
      await message(msg, { title: 'Backend is not running', kind: 'error' });
    } catch (e) {
      console.error('[Tauri] Could not show backend failure dialog:', e);
    }
  };
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    const { listen } = await import('@tauri-apps/api/event');
    await listen<string>('backend-error', (e) => void show(e.payload));
    const existing = await invoke<string | null>('get_backend_error');
    if (existing) void show(existing);
  } catch (e) {
    console.error('[Tauri] Failed to watch for backend errors:', e);
  }
}

/**
 * Open an indexed file in the OS default application.
 *
 * Returns false when there is nothing to open or we are not running under the
 * desktop shell — a browser tab cannot reach the user's filesystem, and callers
 * use that to hide the affordance rather than offer a button that does nothing.
 *
 * Goes through the shell's own `open_file` command, which only opens an existing
 * non-executable file. The plugin-shell JS `open` is URL-only and would reject
 * every filesystem path, and widening its scope would allow arbitrary targets.
 */
export async function openFile(path: string): Promise<boolean> {
  if (!isTauri || !path) return false;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('open_file', { path });
    return true;
  } catch (e) {
    console.error('[Tauri] Failed to open file:', path, e);
    return false;
  }
}

export async function pickFolder(fallbackFetch: () => Promise<{ path: string }>): Promise<{ path: string }> {
  if (isTauri) {
    const { open } = await import('@tauri-apps/plugin-dialog');
    const selected = await open({ directory: true, multiple: false, title: 'Select a folder to index' });
    if (typeof selected === 'string') return { path: selected };
    if (Array.isArray(selected) && (selected as string[]).length > 0) return { path: selected[0] };
    return { path: '' };
  }
  return fallbackFetch();
}
