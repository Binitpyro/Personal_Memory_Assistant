import { describe, it, expect } from 'vitest';
import { formatScore } from '../utils/format';

/**
 * Safelight's score rule: truncate toward zero, never round. A rounded 0.449
 * prints 0.45 and reads as clearing a line it did not reach.
 */
describe('formatScore', () => {
  it('truncates rather than rounds', () => {
    expect(formatScore(0.449)).toBe('0.44');
    expect(formatScore(0.9)).toBe('0.90');
  });

  it('truncates toward zero for negative scores', () => {
    // Reranker logits are negative; -2.017 must not print as -2.02.
    expect(formatScore(-2.017)).toBe('-2.01');
  });

  it('survives binary float error at an exact two-place value', () => {
    // 0.29 * 100 is 28.999999999999996; a bare floor prints 0.28.
    expect(formatScore(0.29)).toBe('0.29');
  });
});
