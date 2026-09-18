import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { useTheme, readChartTokens, type ChartTokens } from '../theme';

/**
 * ECharts paints to a canvas, so no colour here can inherit a CSS variable —
 * every one is handed over as a literal. This chart carried thirteen dark-theme
 * literals (#e2e8f0 label text haloed against #0f172a, an rgba(15,23,42)
 * tooltip), so in Paper it rendered near-white text on a near-white ground.
 *
 * Split out as a pure function of (traceData, tokens) so the palette can be
 * asserted directly — the vitest ECharts mock discards the `option` prop, so
 * there is no way to read it back through the DOM.
 */
export function buildTraceOption(traceData: string, t: ChartTokens) {
  const nodesMap = new Map();
  const links = [];

  const lines = traceData.split('\n');
  for (const line of lines) {
    if (!line.trim()) continue;

    // Parse paths formatted like "Class MyClass -[calls]-> Function MyFunc"
    const parts = line.split(/ -\[(.*?)\]-> /);
    for (let i = 0; i < parts.length; i += 2) {
      const nodeName = parts[i].trim();
      if (!nodesMap.has(nodeName)) {
        let color = '#38bdf8'; // Default cyan
        let size = 20;
        let effect = false;

        // Color coding based on semantic type. These five stay literal: they
        // encode node KIND, not theme, and are a categorical ramp rather than
        // part of the cabinet palette. On a light ground they fail 3:1 as
        // fills (#fbbf24 measures 1.49 against #F7F3E9), which is why the
        // border below is `t.text` — ink on light, bone on dark, so the object
        // boundary carries perceivability in both themes.
        if (nodeName.startsWith('File') || nodeName.startsWith('Folder')) {
           color = '#a78bfa'; // Purple
           size = 35;
        } else if (nodeName.startsWith('Class') || nodeName.startsWith('Struct') || nodeName.startsWith('Trait')) {
           color = '#34d399'; // Emerald
           size = 28;
        } else if (nodeName.startsWith('Function') || nodeName.startsWith('Method')) {
           color = '#f472b6'; // Pink
           size = 22;
        } else if (nodeName.startsWith('Type') || nodeName.startsWith('Interface')) {
           color = '#fbbf24'; // Amber
           size = 24;
        }

        // Make the starting nodes pulse
        if (i === 0) {
          effect = true;
        }

        nodesMap.set(nodeName, {
          name: nodeName,
          symbolSize: size,
          itemStyle: {
            color: color,
            shadowBlur: effect ? 25 : 15,
            shadowColor: color,
            opacity: 0.9,
            borderColor: t.text,
            borderWidth: 1
          },
          label: {
            show: true,
            position: 'bottom',
            color: t.text,
            fontSize: 10,
            textBorderColor: t.bg,
            textBorderWidth: 2,
            formatter: (params: any) => {
              const parts = params.name.split(' ');
              if (parts.length > 1) {
                return parts.slice(1).join(' '); // Hide prefix like "Function"
              }
              return params.name;
            }
          }
        });
      } else if (i === 0) {
         // Ensure it pulses if it's found as a start node again
         const node = nodesMap.get(nodeName);
         node.itemStyle.shadowBlur = 25;
      }

      if (i > 0) {
        const sourceName = parts[i-2].trim();
        const relation = parts[i-1].trim();

        links.push({
          source: sourceName,
          target: nodeName,
          value: relation,
          lineStyle: {
            width: 2,
            curveness: 0.15,
            opacity: 0.6
          }
        });
      }
    }
  }

  const graphNodes = Array.from(nodesMap.values());

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: t.surface,
      borderColor: t.edge,
      textStyle: { color: t.text, fontSize: 12 },
      formatter: (params: any) => {
         if (params.dataType === 'edge') {
           return `${params.data.source.split(' ').slice(1).join(' ')} <br/><span style="color:${t.text2};font-size:10px;">${params.data.value}</span><br/> ⟶ ${params.data.target.split(' ').slice(1).join(' ')}`;
         }
         return params.name;
      }
    },
    animationDurationUpdate: 2000,
    animationEasingUpdate: 'quinticInOut',
    series: [
      {
        type: 'graph',
        layout: 'force',
        force: {
          repulsion: 400,
          edgeLength: [60, 160],
          gravity: 0.1,
          friction: 0.2
        },
        roam: true,
        draggable: true,
        nodes: graphNodes,
        links: links,
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: [0, 8],
        edgeLabel: {
          show: true,
          fontSize: 9,
          formatter: '{c}',
          color: t.text2,
          textBorderColor: t.bg,
          textBorderWidth: 2
        },
        itemStyle: {
          borderColor: t.text,
          borderWidth: 1,
          shadowBlur: 10,
          // Stays literal: a shadow, and correct on either ground.
          shadowColor: 'rgba(0, 0, 0, 0.3)'
        },
        lineStyle: {
          color: 'source',
          curveness: 0.3
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: {
            width: 4
          },
          label: {
            fontSize: 12,
            fontWeight: 'bold'
          }
        }
      }
    ]
  };
}

export const CrystalGraphTrace = ({ traceData }: { traceData: string }) => {
  // The theme is in the deps for a reason: without it the memo keeps the old
  // palette until `traceData` changes, so toggling Cabinet/Paper leaves the
  // chart on the previous theme's colours. Same wiring as FileTypeTreemap.
  const theme = useTheme();
  const options = useMemo(() => buildTraceOption(traceData, readChartTokens()), [traceData, theme]);

  if (!traceData || traceData.trim() === '') {
    return null;
  }

  return (
    <div className="well w-full h-[350px] mt-2 rounded-2xl border border-rule overflow-hidden relative group shadow-2xl">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent pointer-events-none" />
      <ReactECharts
        option={options}
        style={{ height: '100%', width: '100%' }}
        opts={{ renderer: 'canvas' }}
        notMerge={true}
      />
      <div className="absolute bottom-2 left-2 px-2 py-1 bg-surface/90 backdrop-blur-md rounded-md text-xs text-text-secondary border border-rule">
        ✨ 3D Crystal Dreamscape (Hover & Drag)
      </div>
    </div>
  );
};
