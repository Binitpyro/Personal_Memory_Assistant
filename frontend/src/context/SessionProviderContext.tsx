import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';

export interface ModelOverride {
  provider: string;
  model: string;
}

interface SessionProviderContextType {
  sessionModelOverride: ModelOverride | null;
  setSessionModelOverride: (override: ModelOverride | null) => void;
  sessionCost: number;
  weeklyCost: number;
  addSessionCost: (cost: number) => void;
  resetSessionCost: () => void;
}

const SessionProviderContext = createContext<SessionProviderContextType>({
  sessionModelOverride: null,
  setSessionModelOverride: () => {},
  sessionCost: 0,
  weeklyCost: 0,
  addSessionCost: () => {},
  resetSessionCost: () => {},
});

// Helper to get start of current week (Monday)
function getStartOfWeek(date: Date) {
  const d = new Date(date);
  const day = d.getDay() || 7; 
  if (day !== 1) d.setHours(-24 * (day - 1));
  d.setHours(0, 0, 0, 0);
  return d;
}

// Helper to parse stored costs
function getStoredCosts(): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem('pma_historical_costs') || '{}');
  } catch {
    return {};
  }
}

function calculateWeeklyCost() {
  const costs = getStoredCosts();
  const startOfWeek = getStartOfWeek(new Date()).getTime();
  let total = 0;
  for (const [dateStr, cost] of Object.entries(costs)) {
    if (new Date(dateStr).getTime() >= startOfWeek) {
      total += cost;
    }
  }
  return total;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [sessionModelOverride, setSessionModelOverride] = useState<ModelOverride | null>(null);
  const [sessionCost, setSessionCost] = useState(0);
  const [weeklyCost, setWeeklyCost] = useState(calculateWeeklyCost);

  const addSessionCost = (cost: number) => {
    if (cost <= 0) return;
    setSessionCost((prev) => prev + cost);
    setWeeklyCost((prev) => prev + cost);
    
    // Persist to local storage
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const dateStr = today.toISOString();
    
    const costs = getStoredCosts();
    costs[dateStr] = (costs[dateStr] || 0) + cost;
    
    // Optional: cleanup older than 30 days
    const thirtyDaysAgo = Date.now() - 30 * 24 * 60 * 60 * 1000;
    for (const d of Object.keys(costs)) {
      if (new Date(d).getTime() < thirtyDaysAgo) {
        delete costs[d];
      }
    }
    
    localStorage.setItem('pma_historical_costs', JSON.stringify(costs));
  };

  const resetSessionCost = () => {
    setSessionCost(0);
  };

  return (
    <SessionProviderContext.Provider
      value={{
        sessionModelOverride,
        setSessionModelOverride,
        sessionCost,
        weeklyCost,
        addSessionCost,
        resetSessionCost,
      }}
    >
      {children}
    </SessionProviderContext.Provider>
  );
}

export function useSessionProvider() {
  return useContext(SessionProviderContext);
}
