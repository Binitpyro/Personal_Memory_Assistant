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
