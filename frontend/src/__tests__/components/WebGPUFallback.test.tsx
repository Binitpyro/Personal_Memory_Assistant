import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { WebGPUFallback } from '../../components/WebGPUFallback';
import { renderWithProviders } from '../test-utils';

describe('WebGPUFallback Component', () => {
  it('renders status message and canvas container', () => {
    renderWithProviders(
      <WebGPUFallback 
        allFiles={{}}
        activeFilter=""
        onFilterChange={() => {}}
        initialMode="folder"
      />
    );

    // Since navigator.gpu is not available in JSDOM, it transitions immediately to unsupported state
    expect(screen.getByText("Browser doesn't support WebGPU.")).toBeDefined();
  });
});
