/**
 * A8-15: the treemap zoomed by folder NAME, and ECharts resolves a string
 * targetNode to the FIRST node with that name - so ProjectB/src zoomed the
 * chart to ProjectA/src. These run the real buildFolderTree and a real ECharts
 * instance (SSR, no DOM) rather than the setup.ts mock, which cannot resolve a
 * node at all.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import * as echarts from 'echarts/core';
import { TreemapChart } from 'echarts/charts';
import { SVGRenderer } from 'echarts/renderers';
import { buildFolderTree } from '../../utils/treeBuilder';
import { assignFolderIds, zoomTarget, FileTypeTreemap } from '../../components/FileTypeTreemap';
import { renderWithProviders } from '../test-utils';

const dispatched: any[] = [];
vi.mock('echarts-for-react/lib/core', async () => {
    const R = await import('react');
    return {
        default: R.forwardRef(function MockChart(_p: any, ref: any) {
            R.useImperativeHandle(ref, () => ({
                getEchartsInstance: () => ({ dispatchAction: (a: any) => dispatched.push(a) }),
            }));
            return R.createElement('div', { 'data-testid': 'mock-echarts-core' });
        }),
    };
});

echarts.use([TreemapChart, SVGRenderer]);

const FILES = [
    { path: 'C:/Docs/ProjectA/src/a.txt', size: 100, type: '.txt', usage_count: 0 },
    { path: 'C:/Docs/ProjectA/README.md', size: 50, type: '.md', usage_count: 0 },
    { path: 'C:/Docs/ProjectB/src/b.txt', size: 100, type: '.txt', usage_count: 0 },
    { path: 'C:/Docs/ProjectB/README.md', size: 50, type: '.md', usage_count: 0 },
];

function findFolders(node: any, name: string, out: any[] = []): any[] {
    if (node.name === name) out.push(node);
    for (const c of node.children ?? []) findFolders(c, name, out);
    return out;
}

describe('treemap zoom target (A8-15)', () => {
    it('zooms ECharts to the intended duplicate-named folder', () => {
        const tree = [buildFolderTree(FILES, s => Math.sqrt(s + 1) * 10)];
        assignFolderIds(tree);
        const srcs = findFolders(tree[0], 'src');
        expect(srcs.length).toBe(2);
        const wanted = srcs.find(n => n.fullPath.endsWith('ProjectB/src'))!;

        const chart = echarts.init(null as any, undefined, { renderer: 'svg', ssr: true, width: 400, height: 300 });
        chart.setOption({ series: [{ type: 'treemap', data: tree }] });
        chart.dispatchAction({
            type: 'treemapRootToNode',
            targetNode: zoomTarget({ name: 'src', fullPath: wanted.fullPath }, 2),
        });
        const root = (chart as any).getModel().getSeriesByIndex(0).getViewRoot();
        const rawItem = root.hostTree.data.getRawDataItem(root.dataIndex);
        expect(rawItem.fullPath).toBe(wanted.fullPath);
        chart.dispose();
    });

    it('component dispatches a full-path target, not the bare name', () => {
        dispatched.length = 0;
        renderWithProviders(
            <FileTypeTreemap allFiles={{ 'C:/Docs': FILES }} initialMode="folder" />,
        );
        const viewport = screen.getByRole('application', { name: 'File treemap' });
        fireEvent.keyDown(viewport, { key: 'Enter' });
        const zooms = dispatched.filter(a => a.type === 'treemapRootToNode');
        expect(zooms.length).toBe(1);
        expect(zooms[0].targetNode).toMatch(/^C:\/Docs\/Project[AB]$/);
    });
});
