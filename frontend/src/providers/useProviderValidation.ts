import { useState, useCallback } from 'react';
import {
  validateProvider,
  setProviderKey,
  deleteProviderKey,
  setProviderDefaultModel,
  type ValidationResponse,
} from '../api';

export function logProviderHealth(providerId: string, ok: boolean) {
  try {
    const key = `pma_health_${providerId}`;
    const history: boolean[] = JSON.parse(localStorage.getItem(key) || '[]');
    history.push(ok);
    if (history.length > 20) {
      history.shift();
    }
    localStorage.setItem(key, JSON.stringify(history));
    // Dispatch an event so the sparkline can update immediately
    globalThis.dispatchEvent(new CustomEvent('pma-health-updated', { detail: { providerId } }));
  } catch {
    // Ignore storage errors
  }
}

export function useProviderValidation(providerId: string) {
  const [isValidating, setIsValidating] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [validationResult, setValidationResult] = useState<ValidationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const validate = useCallback(async (key: string | null, baseUrl: string | null) => {
    setIsValidating(true);
    setError(null);
    try {
      const res = await validateProvider(providerId, { api_key: key, base_url: baseUrl });
      setValidationResult(res);
      logProviderHealth(providerId, res.ok);
      return res;
    } catch (err: any) {
      const errMsg = err.message || 'Validation failed';
      setError(errMsg);
      const errRes: ValidationResponse = {
        ok: false,
        latency_ms: 0,
        models: [],
        error: errMsg,
        error_code: 'network',
        server_time: null,
      };
      setValidationResult(errRes);
      logProviderHealth(providerId, false);
      return errRes;
    } finally {
      setIsValidating(false);
    }
  }, [providerId]);

  const saveKey = useCallback(async (key: string | null, baseUrl?: string | null) => {
    setIsSaving(true);
    setError(null);
    try {
      await setProviderKey(providerId, key, baseUrl);
      return true;
    } catch (err: any) {
      setError(err.message || 'Failed to save API key');
      return false;
    } finally {
      setIsSaving(false);
    }
  }, [providerId]);

  const deleteKey = useCallback(async () => {
    setIsSaving(true);
    setError(null);
    try {
      await deleteProviderKey(providerId);
      setValidationResult(null);
      return true;
    } catch (err: any) {
      setError(err.message || 'Failed to delete API key');
      return false;
    } finally {
      setIsSaving(false);
    }
  }, [providerId]);

  const saveDefaultModel = useCallback(async (model: string) => {
    try {
      await setProviderDefaultModel(providerId, model);
      return true;
    } catch (err: any) {
      setError(err.message || 'Failed to save default model');
      return false;
    }
  }, [providerId]);

  return {
    isValidating,
    isSaving,
    validationResult,
    error,
    validate,
    saveKey,
    deleteKey,
    saveDefaultModel,
    setValidationResult,
  };
}
