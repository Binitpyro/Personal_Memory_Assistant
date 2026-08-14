import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { WebGPUFallback, resizeTarget } from '../../components/WebGPUFallback';
import { renderWithProviders } from '../test-utils';

describe('resizeTarget', () => {
  // Regression guard for the Insights page growing without bound. The renderer
  // writes canvas.width/height, which are the canvas's intrinsic layout size, so
  // observing the canvas made the ResizeObserver feed its own output back in and
  // multiply by DPR every cycle. jsdom has no layout engine and cannot reproduce
  // the loop, so assert the invariant that prevents it instead.
  it('never returns the canvas itself', () => {
    const wrapper = document.createElement('div');
    const canvas = document.createElement('canvas');
    wrapper.appendChild(canvas);

    expect(resizeTarget(canvas, wrapper)).toBe(wrapper);
    expect(resizeTarget(canvas, null)).toBe(wrapper);
    // A caller that mistakenly passes the canvas as its own wrapper must get
    // nothing back rather than re-arming the loop.
    expect(resizeTarget(canvas, canvas)).toBeNull();
  });

  it('returns null rather than the canvas when it has no parent', () => {
    const orphan = document.createElement('canvas');
    expect(resizeTarget(orphan, null)).toBeNull();
  });
});

describe('WebGPUFallback Component', () => {
  it('renders status message and canvas container', async () => {
    renderWithProviders(
      <WebGPUFallback 
        allFiles={{}}
        activeFilter=""
        onFilterChange={() => {}}
        initialMode="folder"
      />
    );

    // Since navigator.gpu is not available in JSDOM, it transitions to unsupported state
    const fallbackText = await screen.findByText(/Neither WebGPU nor WebGL2 available./i);
    expect(fallbackText).toBeDefined();
  });
});
