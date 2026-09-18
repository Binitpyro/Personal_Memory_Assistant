import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';

export interface ModelOverride {
  provider: string;
  model: string;
}

/** A model the user has actually run, with how often, over the retained window. */
export interface ModelUsage extends ModelOverride {
  count: number;
}

interface SessionProviderContextType {
  sessionModelOverride: ModelOverride | null;
  setSessionModelOverride: (override: ModelOverride | null) => void;
  sessionCost: number;
  weeklyCost: number;
  addSessionCost: (cost: number) => void;
  resetSessionCost: () => void;
  /**
   * One answer was produced by this provider/model.
   *
   * Deliberately NOT folded into addSessionCost, which returns early on
   * `cost <= 0` — every local model is free, so counting there would record
   * cloud usage only and miss exactly the models this product exists to serve.
   */
  recordModelUse: (provider: string, model: string) => void;
  mostUsedModel: ModelUsage | null;
}

const SessionProviderContext = createContext<SessionProviderContextType>({
  sessionModelOverride: null,
  setSessionModelOverride: () => {},
  sessionCost: 0,
  weeklyCost: 0,
  addSessionCost: () => {},
  resetSessionCost: () => {},
  recordModelUse: () => {},
  mostUsedModel: null,
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

const MODEL_USAGE_KEY = 'pma_model_usage';
const USAGE_SEP = '::';

/** `{ "<ISO date>": { "<provider>::<model>": count } }` — same date-bucketed
 *  shape and 30-day window as the cost log above, so both prune identically. */
type ModelUsageLog = Record<string, Record<string, number>>;

function getStoredUsage(): ModelUsageLog {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(MODEL_USAGE_KEY) || '{}');
    return raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as ModelUsageLog) : {};
  } catch {
    return {};
  }
}

/**
 * The most-run model across the retained window.
 *
 * Ties break on most-recent use, not on object key order, so the answer does
 * not depend on JSON property ordering. Exported for its own test: the winner
 * has to be a function of the data, and a bucketed sum is not obvious enough
 * to leave unchecked.
 */
export function selectMostUsedModel(log: ModelUsageLog): ModelUsage | null {
  const totals = new Map<string, { count: number; last: string }>();
  for (const [dateStr, models] of Object.entries(log)) {
    if (!models || typeof models !== 'object') continue;
    for (const [key, n] of Object.entries(models)) {
      if (typeof n !== 'number' || !Number.isFinite(n) || n <= 0) continue;
      const prev = totals.get(key);
      totals.set(key, {
        count: (prev?.count ?? 0) + n,
        last: !prev || dateStr > prev.last ? dateStr : prev.last,
      });
    }
  }

  let bestKey: string | null = null;
  let best: { count: number; last: string } | null = null;
  for (const [key, v] of totals) {
    if (!best || v.count > best.count || (v.count === best.count && v.last > best.last)) {
      best = v;
      bestKey = key;
    }
  }
  if (!bestKey || !best) return null;

  // Only the FIRST separator splits: an OpenAI-compatible model id may itself
  // contain a colon (`gemma4-local:latest`), so splitting greedily would cut it.
  const i = bestKey.indexOf(USAGE_SEP);
  if (i < 0) return null;
  return { provider: bestKey.slice(0, i), model: bestKey.slice(i + USAGE_SEP.length), count: best.count };
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
  const [mostUsedModel, setMostUsedModel] = useState<ModelUsage | null>(() =>
    selectMostUsedModel(getStoredUsage()),
  );

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

  const recordModelUse = (provider: string, model: string) => {
    // 'unknown' is what the resolver falls back to when nothing is configured.
    // Recording it would invent a model the user never ran.
    if (!provider || !model || provider === 'unknown' || model === 'unknown') return;

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const dateStr = today.toISOString();

    const log = getStoredUsage();
    const bucket = log[dateStr] ?? {};
    bucket[`${provider}${USAGE_SEP}${model}`] = (bucket[`${provider}${USAGE_SEP}${model}`] || 0) + 1;
    log[dateStr] = bucket;

    const thirtyDaysAgo = Date.now() - 30 * 24 * 60 * 60 * 1000;
    for (const d of Object.keys(log)) {
      if (new Date(d).getTime() < thirtyDaysAgo) delete log[d];
    }

    try {
      localStorage.setItem(MODEL_USAGE_KEY, JSON.stringify(log));
    } catch { /* private mode — the in-memory tally below still updates */ }
    setMostUsedModel(selectMostUsedModel(log));
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
        recordModelUse,
        mostUsedModel,
      }}
    >
      {children}
    </SessionProviderContext.Provider>
  );
}

export function useSessionProvider() {
  return useContext(SessionProviderContext);
}
