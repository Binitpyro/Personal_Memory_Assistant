import { useEffect, useState } from 'react';

export function ProviderSparkline({ providerId }: { providerId: string }) {
  const [history, setHistory] = useState<boolean[]>([]);

  useEffect(() => {
    const load = () => {
      try {
        const data = JSON.parse(localStorage.getItem(`pma_health_${providerId}`) || '[]');
        setHistory(data);
      } catch {
        setHistory([]);
      }
    };
    load();

    const handler = (e: any) => {
      if (e.detail?.providerId === providerId) {
        load();
      }
    };
    globalThis.addEventListener('pma-health-updated', handler);
    return () => globalThis.removeEventListener('pma-health-updated', handler);
  }, [providerId]);

  if (history.length === 0) return null;

  return (
    <div className="flex items-center gap-0.5 mt-1" title="Last 20 validations">
      {history.map((ok, i) => (
        <div
          key={i}
          className={`w-1 h-1 rounded-sm ${ok ? 'bg-success' : 'bg-danger'}`}
          style={{ opacity: 0.3 + (0.7 * (i + 1) / history.length) }}
        />
      ))}
    </div>
  );
}
