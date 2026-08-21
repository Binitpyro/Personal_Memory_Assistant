import { useEffect, useState } from 'react';
import { Network, Loader2, Library } from 'lucide-react';
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
        <Loader2 className="w-8 h-8 text-primary animate-spin" />
        <p className="text-text-secondary text-sm">Synthesizing knowledge portrait...</p>
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
        <Library className="w-12 h-12 text-text-muted mx-auto mb-3" />
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
        <Network className="w-5 h-5 text-primary" />
        <h2 className="text-xl font-bold text-text-primary">Knowledge Portrait</h2>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {themes.map((theme, idx) => (
          <div key={idx} className="bg-bg-dark rounded-xl p-5 border border-border flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-lg font-semibold text-primary">{theme.name}</h3>
                <div className="px-2 py-1 bg-primary/10 text-primary text-xs rounded-full font-mono">
                  W:{theme.weight}
                </div>
              </div>
              <p className="text-text-secondary text-sm leading-relaxed">
                {theme.description}
              </p>
            </div>
            {/* simple weight bar */}
            <div className="mt-4 h-1.5 w-full bg-border rounded-full overflow-hidden">
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
