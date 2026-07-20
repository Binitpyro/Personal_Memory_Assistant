export function validateApiKeyFormat(providerId: string, key: string): { isValid: boolean; helperText?: string } {
  if (!key) {
    return { isValid: false, helperText: 'API Key is required.' };
  }

  // If key is redacted (contains dots/asterisks or doesn't match standard format but is pre-existing), we treat it as valid format.
  if (key.includes('••••') || key.includes('****')) {
    return { isValid: true };
  }

  switch (providerId) {
    case 'gemini': {
      const regex = /^AIza[0-9A-Za-z_-]{35}$/;
      const ok = regex.test(key);
      return {
        isValid: ok,
        helperText: ok ? undefined : 'Gemini API keys should start with "AIza" and be 39 characters long.',
      };
    }
    case 'openai': {
      const regex = /^sk-[A-Za-z0-9_-]{20,}$/;
      const ok = regex.test(key);
      return {
        isValid: ok,
        helperText: ok ? undefined : 'OpenAI API keys should start with "sk-".',
      };
    }
    case 'anthropic': {
      const regex = /^sk-ant-[A-Za-z0-9_-]{20,}$/;
      const ok = regex.test(key);
      return {
        isValid: ok,
        helperText: ok ? undefined : 'Anthropic API keys should start with "sk-ant-".',
      };
    }
    case 'groq': {
      const regex = /^gsk_[A-Za-z0-9]{20,}$/;
      const ok = regex.test(key);
      return {
        isValid: ok,
        helperText: ok ? undefined : 'Groq API keys should start with "gsk_".',
      };
    }
    case 'openrouter': {
      const regex = /^sk-or-v1-[A-Za-z0-9]+$/;
      const ok = regex.test(key);
      return {
        isValid: ok,
        helperText: ok ? undefined : 'OpenRouter API keys should start with "sk-or-v1-".',
      };
    }
    case 'nvidia_nim': {
      const regex = /^nvapi-[A-Za-z0-9_-]{20,}$/;
      const ok = regex.test(key);
      return {
        isValid: ok,
        helperText: ok ? undefined : 'NVIDIA NIM API keys should start with "nvapi-".',
      };
    }
    default:
      return { isValid: true };
  }
}
