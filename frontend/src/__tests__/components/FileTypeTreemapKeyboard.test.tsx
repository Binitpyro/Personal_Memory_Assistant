/**
 * Keyboard navigation of the treemap.
 *
 * The treemap gets the Outliner half of the keymap and none of the camera
 * half — it has no camera. `F` is deliberately absent here rather than present
 * and dead.
 *
 * The ECharts mock in `__tests__/setup.ts` renders a plain div with no ref, so
 * `getEchartsInstance()` is undefined throughout. That is a fair test of the
 * production path rather than a limitation to work around: `navPath` is the
 * source of truth and the chart dispatch is its visual echo, so navigation has
 * to work whether or not the chart instance happens to exist yet.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { FileTypeTreemap } from '../../components/FileTypeTreemap';
import { renderWithProviders } from '../test-utils';

const FILES = {
    'C:/projects/test': [
        { path: 'C:/projects/test/alpha/one.txt', size: 100, type: 'txt', usage_count: 0 },
        { path: 'C:/projects/test/alpha/two.txt', size: 200, type: 'txt', usage_count: 0 },
        { path: 'C:/projects/test/beta/three.py', size: 300, type: 'py', usage_count: 2 },
    ],
};

const viewport = () => screen.getByRole('application', { name: 'File treemap' });
const status = () => document.querySelector('[aria-live="polite"]') as HTMLElement;

function renderMap() {
    renderWithProviders(<FileTypeTreemap allFiles={FILES} initialMode="folder" />);
}

describe('treemap keyboard', () => {
    it('exposes the chart as a focusable, named viewport', () => {
        renderMap();
        const el = viewport();
        expect(el.getAttribute('tabindex')).toBe('0');
        expect(el.getAttribute('aria-describedby')).toBe('treemap-keyhint');
    });

    it('mirrors the current level as an accessible tree', () => {
        renderMap();
        const tree = screen.getByRole('tree', { name: 'Treemap level' });
        expect(tree).toBeDefined();
        expect(screen.getAllByRole('treeitem').length).toBeGreaterThan(0);
    });

    it('says nothing until something happens', () => {
        renderMap();
        // A live region that narrates on mount announces the page to a screen
        // reader user who has not yet interacted with it.
        expect(status().textContent).toBe('');
    });

    it('announces the node under the cursor with its position in the level', () => {
        renderMap();
        fireEvent.keyDown(viewport(), { key: 'ArrowDown' });
        expect(status().textContent).toMatch(/, (folder|file), .*\d+ of \d+/);
    });

    it('moves the cursor down and announces the new node', () => {
        renderMap();
        const before = status().textContent;
        fireEvent.keyDown(viewport(), { key: 'ArrowDown' });
        expect(status().textContent).not.toBe(before);
        expect(status().textContent).toMatch(/2 of/);
    });

    it('clamps at both ends rather than wrapping, and says so', () => {
        renderMap();
        fireEvent.keyDown(viewport(), { key: 'ArrowUp' });
        expect(status().textContent).toBe('First item');

        // Walk past the end of a two-item level.
        fireEvent.keyDown(viewport(), { key: 'ArrowDown' });
        fireEvent.keyDown(viewport(), { key: 'ArrowDown' });
        fireEvent.keyDown(viewport(), { key: 'ArrowDown' });
        expect(status().textContent).toBe('Last item');
    });

    it('descends on Enter and returns on Backspace', () => {
        renderMap();
        const levelBefore = screen.getAllByRole('treeitem').length;

        fireEvent.keyDown(viewport(), { key: 'Enter' });
        // The breadcrumb is the visible proof the level changed.
        expect(status().textContent).toMatch(/^Entered /);

        fireEvent.keyDown(viewport(), { key: 'Backspace' });
        expect(status().textContent).toBe('Up one level');
        expect(screen.getAllByRole('treeitem').length).toBe(levelBefore);
    });

    it('returns to the root on Home', () => {
        renderMap();
        fireEvent.keyDown(viewport(), { key: 'Enter' });
        fireEvent.keyDown(viewport(), { key: 'Home' });
        expect(status().textContent).toBe('Back to root');
    });

    it('opens the reference on ? and omits the camera half', () => {
        renderMap();
        fireEvent.keyDown(viewport(), { key: '?' });

        const dialog = document.querySelector('dialog');
        expect(dialog).not.toBeNull();
        const text = dialog!.textContent ?? '';
        expect(text).toContain('Hierarchy');
        // The treemap has no camera: these would be dead keys on a help screen.
        expect(text).not.toContain('Viewport');
        expect(text).not.toContain('Frame the selection');
    });

    it('ignores keys it does not bind', () => {
        renderMap();
        const before = status().textContent;
        fireEvent.keyDown(viewport(), { key: 'w' });
        expect(status().textContent).toBe(before);
    });
});

describe('treemap mouse path is unchanged', () => {
    it('still renders the chart and the mode toggles', () => {
        const onFilterChange = vi.fn();
        renderWithProviders(
            <FileTypeTreemap allFiles={FILES} onFilterChange={onFilterChange} initialMode="folder" />,
        );
        expect(screen.getByTestId('mock-echarts-core')).toBeDefined();
        expect(screen.getByText('BY FOLDERS')).toBeDefined();
        expect(screen.getByText('BY FILE TYPE')).toBeDefined();
    });
});
