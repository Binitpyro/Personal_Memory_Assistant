import React from 'react';
import {
  Sparkles,
  Cpu,
  Fingerprint,
  Zap,
  Globe,
  CircleDot,
  Terminal,
  Monitor,
  Settings,
} from 'lucide-react';

/**
 * Provider marks.
 *
 * These used to carry nine hardcoded palette hues — indigo, emerald, orange,
 * amber, sky, green, zinc, blue, slate — none of which are tokens. Two problems
 * with that. It put nine differently-lit accents on one list, against a system
 * whose whole colour rule is one accent; and the values were authored for the
 * dark theme only, so `text-amber-400` on Paper's `#F7F3E9` ground measured
 * about 1.7 and the mark all but vanished.
 *
 * The glyph already distinguishes the provider. Colour is left to the caller
 * via `currentColor`, so a selected row can brighten its mark and a resting one
 * can stay quiet, in whichever theme is active.
 */
interface ProviderIconProps {
  id: string;
  className?: string;
}

export const ProviderIcon: React.FC<ProviderIconProps> = ({ id, className = 'w-5 h-5' }) => {
  switch (id) {
    case 'gemini':
      return <Sparkles className={className} />;
    case 'openai':
      return <Cpu className={className} />;
    case 'anthropic':
      return <Fingerprint className={className} />;
    case 'groq':
      return <Zap className={className} />;
    case 'openrouter':
      return <Globe className={className} />;
    case 'nvidia_nim':
      return <CircleDot className={className} />;
    case 'ollama':
      return <Terminal className={className} />;
    case 'lm_studio':
      return <Monitor className={className} />;
    case 'openai_compatible':
    default:
      return <Settings className={className} />;
  }
};
