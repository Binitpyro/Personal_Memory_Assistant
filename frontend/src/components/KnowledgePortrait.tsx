import { useEffect, useState } from 'react';
import { getPortrait, type PortraitTheme } from '../api';

export function KnowledgePortrait() {
  const [themes, setThemes] = useState<PortraitTheme[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getPortrait()
      .then((data) => {
        if (active) {
          setThemes(data.themes || []);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err.message || String(err));
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (loading) {
    return (
      <div className="glass-card flex flex-col items-center justify-center py-12 space-y-4">
        <p className="edge-type text-text-tertiary" role="status">Synthesizing knowledge portrait…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="glass-card bg-error/10 text-error text-sm p-4 rounded-xl border border-error/20">
        Failed to load knowledge portrait: {error}
      </div>
    );
  }

  if (themes.length === 0) {
    return (
      <div className="glass-card p-8 text-center border border-border">
        <h3 className="text-lg font-medium text-text-primary">No Portrait Available</h3>
        <p className="text-text-secondary text-sm mt-1">
          Not enough data has been indexed to generate a knowledge portrait yet.
        </p>
      </div>
    );
  }

  return (
    <div className="glass-card p-6 border border-border">
      <div className="flex items-center gap-2 mb-6">
        <h2 className="font-bold [font-stretch:80%] text-lg text-text-primary m-0">Knowledge Portrait</h2>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {themes.map((theme, idx) => (
          <div key={idx} className="bg-bg-dark rounded-xl p-5 border border-border flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-lg font-semibold text-primary">{theme.name}</h3>
                <div className="px-2 py-1 border border-rule text-text-secondary text-xs font-mono">
                  W:{theme.weight}
                </div>
              </div>
              <p className="text-text-secondary text-sm leading-relaxed">
                {theme.description}
              </p>
            </div>
            {/* simple weight bar */}
            <div className="mt-4 h-1.5 w-full bg-border overflow-hidden">
              <div 
                className="h-full bg-primary transition-all duration-1000 ease-out" 
                style={{ width: `${(theme.weight / 10) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
