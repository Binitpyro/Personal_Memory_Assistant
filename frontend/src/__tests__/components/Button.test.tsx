import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Button } from '../../components/ui/Button';

/**
 * The loading gate.
 *
 * `Button` documents that "a loading control is not a target: it already
 * accepted the click". That was false whenever a caller also passed `disabled`,
 * because the gate read `disabled ?? loading` - and `??` only falls through on
 * null/undefined, so an explicit `disabled={false}` won and `loading` was
 * discarded entirely.
 *
 * That combination is the normal shape for a form submit
 * (`disabled={!valid} loading={saving}`), so the affected controls stayed
 * clickable for the whole request. It went unnoticed because LibraryPage was
 * the primitive's only consumer and never passed `loading`.
 *
 * Negative control: restore `??` and `combines both props` fails while the
 * other three still pass.
 */
describe('Button loading/disabled gate', () => {
  it('is disabled while loading, with no disabled prop passed', () => {
    render(<Button loading>Save</Button>);
    expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(true);
  });

  it('combines both props rather than letting disabled={false} win', () => {
    // The regression case. `disabled={false} loading` must still be disabled.
    render(<Button disabled={false} loading>Save</Button>);
    expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(true);
  });

  it('does not fire onClick while loading', () => {
    const onClick = vi.fn();
    render(<Button disabled={false} loading onClick={onClick}>Save</Button>);
    fireEvent.click(screen.getByRole('button'));
    expect(onClick).not.toHaveBeenCalled();
  });

  it('stays enabled and clickable when neither prop is set', () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Save</Button>);
    const btn = screen.getByRole('button') as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('marks a loading control busy for assistive tech', () => {
    render(<Button loading>Save</Button>);
    expect(screen.getByRole('button').getAttribute('aria-busy')).toBe('true');
  });
});
