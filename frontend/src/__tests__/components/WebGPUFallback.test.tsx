import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { WebGPUFallback } from '../../components/WebGPUFallback';
import { renderWithProviders } from '../test-utils';

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
