/**
 * The model picker's dialog lifecycle.
 *
 * The picker moved from a hand-rolled fixed overlay to a native <dialog> +
 * showModal(), which buys the focus trap, Escape and the inert background for
 * free. The cost is one sharp edge, and this file exists for it:
 *
 *   Escape closes the dialog through the USER AGENT, not through React. If that
 *   close is not mirrored back into `isOpen`, the state stays true, the
 *   trigger's setIsOpen(true) becomes a no-op, the open-effect never re-runs,
 *   and the palette can never be reopened without a full page reload.
 *
 * What this file locks is that contract — a close event leaves the component
 * reopenable — NOT one particular implementation of it. Both wirings satisfy
 * it: these pass with the shipped native listener and also with React's
 * `onClose`, which was checked by reverting. So do not read a pass here as
 * evidence that the native listener is required; read it as evidence that the
 * close-to-state path exists at all, which is the part that silently breaks.
 *
 * The tests dispatch `close` on the element rather than calling `close()` or
 * pressing Escape, because jsdom has no real dialog behaviour and the embedded
 * browser used for manual checks fires no `close` event even for a bare,
 * framework-free dialog.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { ModelPicker } from '../../components/providers/ModelPicker';

vi.mock('../../api', () => ({
  getProviders: vi.fn().mockResolvedValue([]),
}));

vi.mock('../../useApi', () => ({
  useApi: () => ({ data: [], refetch: vi.fn(), loading: false, error: null }),
  invalidateCache: vi.fn(),
}));

vi.mock('../../context/SessionProviderContext', () => ({
  useSessionProvider: () => ({
    sessionModelOverride: null,
    setSessionModelOverride: vi.fn(),
  }),
}));

// jsdom has no layout engine behind <dialog>; these only need to record state.
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    this.open = false;
    this.dispatchEvent(new Event('close'));
  };
});

const trigger = () => screen.getByTitle(/Change active session model/i);
const dialog = () => document.querySelector('dialog') as HTMLDialogElement;

describe('ModelPicker dialog lifecycle', () => {
  it('is mounted but closed before the trigger is used', () => {
    render(<ModelPicker />);
    expect(dialog()).not.toBeNull();
    expect(dialog().open).toBe(false);
  });

  it('opens as a modal from the trigger', () => {
    render(<ModelPicker />);
    fireEvent.click(trigger());
    expect(dialog().open).toBe(true);
  });

  it('reopens after the user agent closes it, as Escape does', () => {
    render(<ModelPicker />);

    fireEvent.click(trigger());
    expect(dialog().open).toBe(true);

    // Exactly what Escape produces: the dialog closes itself and React is only
    // told via the DOM event.
    act(() => {
      dialog().open = false;
      dialog().dispatchEvent(new Event('close'));
    });
    expect(dialog().open).toBe(false);

    // The regression: with `isOpen` left stale at true this click did nothing.
    fireEvent.click(trigger());
    expect(dialog().open).toBe(true);
  });

  it('returns focus to the trigger when the dialog closes', () => {
    render(<ModelPicker />);
    fireEvent.click(trigger());

    act(() => {
      dialog().open = false;
      dialog().dispatchEvent(new Event('close'));
    });

    expect(document.activeElement).toBe(trigger());
  });
});
