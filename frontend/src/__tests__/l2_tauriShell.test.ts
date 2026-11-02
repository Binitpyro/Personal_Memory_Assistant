import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const invoke = vi.fn()
const listen = vi.fn()
const message = vi.fn()

vi.mock('@tauri-apps/api/core', () => ({ invoke }))
vi.mock('@tauri-apps/api/event', () => ({ listen }))
vi.mock('@tauri-apps/plugin-dialog', () => ({ message }))

async function connect() {
  const { initTauriConnection } = await import('../utils/tauriShell')
  await initTauriConnection(() => {}, () => {})
  await new Promise((r) => setTimeout(r, 0))
}

describe('initTauriConnection backend failure surfacing (A8-18)', () => {
  beforeEach(() => {
    vi.resetModules()
    invoke.mockReset()
    listen.mockReset()
    message.mockReset()
    ;(globalThis as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  })
  afterEach(() => {
    delete (globalThis as Record<string, unknown>).__TAURI_INTERNALS__
  })

  it('shows a failure recorded before the webview was listening', async () => {
    invoke.mockImplementation(async (cmd: string) =>
      cmd === 'get_backend_info' ? [1234, 'tok'] : 'Bundled sidecar not found at X',
    )
    await connect()
    expect(message).toHaveBeenCalledTimes(1)
    expect(message.mock.calls[0][0]).toBe('Bundled sidecar not found at X')
  })

  it('shows a later backend-error event once', async () => {
    invoke.mockImplementation(async (cmd: string) => (cmd === 'get_backend_info' ? [1234, 'tok'] : null))
    let handler: ((e: { payload: string }) => void) | undefined
    listen.mockImplementation(async (_n: string, cb: (e: { payload: string }) => void) => {
      handler = cb
      return () => {}
    })
    await connect()
    expect(message).not.toHaveBeenCalled()
    handler?.({ payload: 'Backend exited unexpectedly (exit code Some(1))' })
    handler?.({ payload: 'again' })
    await new Promise((r) => setTimeout(r, 0))
    expect(message).toHaveBeenCalledTimes(1)
  })
})

describe('openFile (A8-19)', () => {
  beforeEach(() => {
    vi.resetModules()
    invoke.mockReset()
    ;(globalThis as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  })
  afterEach(() => {
    delete (globalThis as Record<string, unknown>).__TAURI_INTERNALS__
  })

  it('opens a filesystem path through the shell open_file command', async () => {
    invoke.mockResolvedValue(undefined)
    const { openFile } = await import('../utils/tauriShell')
    expect(await openFile('C:/Users/me/report.pdf')).toBe(true)
    expect(invoke).toHaveBeenCalledWith('open_file', { path: 'C:/Users/me/report.pdf' })
  })

  it('reports false when the shell refuses', async () => {
    invoke.mockRejectedValue('refusing to launch .exe file')
    const { openFile } = await import('../utils/tauriShell')
    expect(await openFile('/x/a.exe')).toBe(false)
  })
})
