import { isTauri } from './tauriShell';

// TODO(Security): The current approach passes the `client_secret` indirectly or 
// relies on a token approach that needs proper backend proxying or OS keychain storage
// before public release. This is a known architectural debt.
export function getGoogleAuthStartUrl(endpoint: string, localToken: string): string {
  const url = new URL(`${endpoint}/api/auth/google/start`);
  if (localToken) url.searchParams.set('token', localToken);
  return url.toString();
}

export async function launchGoogleAuth(endpoint: string, localToken: string): Promise<void> {
  const url = getGoogleAuthStartUrl(endpoint, localToken);

  if (isTauri) {
    const { open } = await import('@tauri-apps/plugin-shell');
    await open(url);
    return;
  }

  const popup = globalThis.open(url, '_blank', 'noopener,noreferrer');
  if (!popup) {
    globalThis.location.assign(url);
  }
}
