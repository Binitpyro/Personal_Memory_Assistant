import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { CrystalGraphTrace, buildTraceOption } from '../../components/CrystalGraphTrace';
import type { ChartTokens } from '../../theme';
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

/**
 * The chart is a canvas, so ECharts is handed literal colours and cannot
 * inherit a CSS variable. Thirteen of those literals were dark-theme values —
 * #e2e8f0 label text haloed against #0f172a, an rgba(15,23,42) tooltip — which
 * in Paper put near-white text on a near-white ground.
 *
 * Asserted against `buildTraceOption` rather than through the DOM because the
 * vitest ECharts mock (`__tests__/setup.ts:85`) serialises `{}` and throws the
 * real option away, so there is nothing to read back from the rendered output.
 */
describe('buildTraceOption theming', () => {
  const TOKENS: ChartTokens = {
    surface: '#111111',
    raised: '#222222',
    bg: '#333333',
    rule: '#444444',
    edge: '#555555',
    text: '#666666',
    text2: '#777777',
    text3: '#888888',
    accent: '#999999',
    plate: '#aaaaaa',
  };

  const TRACE =
    'Class MyClass -[calls]-> Function MyFunc\nFolder MyFolder -[contains]-> File file.txt';

  /** Every string anywhere in the option, so nothing hides in a nested key. */
  function strings(value: unknown): string[] {
    if (typeof value === 'string') return [value];
    if (Array.isArray(value)) return value.flatMap(strings);
    if (value && typeof value === 'object') return Object.values(value).flatMap(strings);
    return [];
  }

  it('carries no dark-theme literals once tokens are supplied', () => {
    const all = strings(buildTraceOption(TRACE, TOKENS)).join(' ').toLowerCase();

    // The exact values the chart used to hardcode.
    for (const dead of ['#e2e8f0', '#0f172a', '#f8fafc', '#94a3b8', '#64748b', '#ffffff', 'rgba(15, 23, 42']) {
      expect(all).not.toContain(dead);
    }
  });

  it('takes its chrome from the tokens it is given', () => {
    const option = buildTraceOption(TRACE, TOKENS) as any;
    const node = option.series[0].nodes[0];

    // Label ink and its halo: the pair that inverted in Paper.
    expect(node.label.color).toBe(TOKENS.text);
    expect(node.label.textBorderColor).toBe(TOKENS.bg);

    // The node outline is what carries perceivability on a light ground,
    // because the categorical fills fail 3:1 there.
    expect(node.itemStyle.borderColor).toBe(TOKENS.text);

    expect(option.tooltip.backgroundColor).toBe(TOKENS.surface);
    expect(option.tooltip.borderColor).toBe(TOKENS.edge);
    expect(option.tooltip.textStyle.color).toBe(TOKENS.text);

    expect(option.series[0].edgeLabel.color).toBe(TOKENS.text2);
    expect(option.series[0].edgeLabel.textBorderColor).toBe(TOKENS.bg);
    expect(option.series[0].itemStyle.borderColor).toBe(TOKENS.text);
  });

  it('keeps the categorical node fills literal', () => {
    // These encode node KIND, not theme, so they must NOT follow the palette.
    const option = buildTraceOption(TRACE, TOKENS) as any;
    const fills = option.series[0].nodes.map((n: any) => n.itemStyle.color);

    expect(fills).toContain('#34d399'); // Class
    expect(fills).toContain('#a78bfa'); // Folder / File
    for (const fill of fills) {
      expect(Object.values(TOKENS)).not.toContain(fill);
    }
  });

  it('still parses the trace into nodes and edges', () => {
    const option = buildTraceOption(TRACE, TOKENS) as any;

    // Two lines, two nodes each, no shared names.
    expect(option.series[0].nodes).toHaveLength(4);
    expect(option.series[0].links).toHaveLength(2);
    expect(option.series[0].links[0].value).toBe('calls');
  });
});
