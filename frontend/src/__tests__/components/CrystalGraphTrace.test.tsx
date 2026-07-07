import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { CrystalGraphTrace } from '../../components/CrystalGraphTrace';
import { renderWithProviders } from '../test-utils';

describe('CrystalGraphTrace Component', () => {
  it('renders tracing steps and timeline', () => {
    const traceDataStr = 'Class MyClass -[calls]-> Function MyFunc\nFolder MyFolder -[contains]-> File file.txt';
    renderWithProviders(
      <CrystalGraphTrace 
        traceData={traceDataStr}
      />
    );

    // Verify it renders the echarts container
    expect(screen.getByTestId('mock-echarts')).toBeDefined();
  });
});
