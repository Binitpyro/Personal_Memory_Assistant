import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { MessageBubble } from '../../components/chat/MessageBubble';
import { renderWithProviders } from '../test-utils';
import { type Message } from '../../hooks/useChatStream';

/**
 * The answer is derived from documents the user did not write, and rehypeRaw
 * turns HTML in it into real DOM. A poisoned chunk can steer the model into
 * emitting an inline handler, and window.__PMA_TOKEN__ sits in the same page.
 *
 * The CSP blocks the exfiltration channels in a browser, but Tauri ships
 * script-src 'self' 'unsafe-inline', so the desktop app has no such backstop.
 */

const assistant = (content: string): Message => ({ id: 'x', role: 'assistant', content });

const render = (content: string) =>
  renderWithProviders(<MessageBubble message={assistant(content)} onNearMissClick={vi.fn()} />);

describe('MessageBubble sanitises model output', () => {
  it('strips inline event handlers', () => {
    const { container } = render('Here you go: <img src="x" onerror="window.__pwned = 1">');

    for (const el of container.querySelectorAll('*')) {
      for (const attr of el.attributes) {
        expect(attr.name.toLowerCase().startsWith('on')).toBe(false);
      }
    }
    expect((globalThis as Record<string, unknown>).__pwned).toBeUndefined();
  });

  it('drops script elements entirely', () => {
    const { container } = render('Answer.<script>window.__pwned = 1</script>');

    expect(container.querySelector('script')).toBeNull();
    expect((globalThis as Record<string, unknown>).__pwned).toBeUndefined();
  });

  it('drops iframes and javascript: hrefs', () => {
    const { container } = render(
      '<iframe src="https://evil.example"></iframe>[click](javascript:alert(1))'
    );

    expect(container.querySelector('iframe')).toBeNull();
    const hrefs = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href') ?? '');
    expect(hrefs.some((h) => h.toLowerCase().startsWith('javascript:'))).toBe(false);
  });

  it('still renders the claim citation UI', () => {
    // The reason rehypeRaw cannot simply be removed: llm_client.py asks the
    // model for these tags and the components map turns them into the
    // grounding affordance. Sanitising must not take the feature with it.
    render('<claim sources="[1][2][3]">Python was created in 1991</claim>.');

    const claim = screen.getByText('Python was created in 1991');
    expect(claim).toBeDefined();
    expect(claim.getAttribute('title')).toContain('[1][2][3]');
  });

  it('still renders ordinary markdown', () => {
    const { container } = render('**bold** and `code`\n\n- one\n- two');

    expect(container.querySelector('strong')?.textContent).toBe('bold');
    expect(container.querySelector('code')?.textContent).toBe('code');
    expect(container.querySelectorAll('li').length).toBe(2);
  });
});
