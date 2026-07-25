import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';

export const CrystalGraphTrace = ({ traceData }: { traceData: string }) => {
  const options = useMemo(() => {
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

          // Color coding based on semantic type
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
              borderColor: '#ffffff',
              borderWidth: 1
            },
            label: { 
              show: true, 
              position: 'bottom', 
              color: '#e2e8f0', 
              fontSize: 10,
              textBorderColor: '#0f172a',
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
        backgroundColor: 'rgba(15, 23, 42, 0.9)',
        borderColor: 'rgba(56, 189, 248, 0.3)',
        textStyle: { color: '#f8fafc', fontSize: 12 },
        formatter: (params: any) => {
           if (params.dataType === 'edge') {
             return `${params.data.source.split(' ').slice(1).join(' ')} <br/><span style="color:#94a3b8;font-size:10px;">${params.data.value}</span><br/> ⟶ ${params.data.target.split(' ').slice(1).join(' ')}`;
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
            color: '#64748b',
            textBorderColor: '#0f172a',
            textBorderWidth: 2
          },
          itemStyle: {
            borderColor: '#fff',
            borderWidth: 1,
            shadowBlur: 10,
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
  }, [traceData]);

  if (!traceData || traceData.trim() === '') {
    return null;
  }

  return (
    <div className="w-full h-[350px] mt-2 rounded-2xl border border-white/10 bg-black/40 overflow-hidden relative group shadow-2xl">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent pointer-events-none" />
      <ReactECharts 
        option={options} 
        style={{ height: '100%', width: '100%' }} 
        opts={{ renderer: 'canvas' }} 
        notMerge={true}
      />
      <div className="absolute bottom-2 left-2 px-2 py-1 bg-black/60 backdrop-blur-md rounded-md text-[10px] text-white/70 border border-white/10">
        ✨ 3D Crystal Dreamscape (Hover & Drag)
      </div>
    </div>
  );
};
