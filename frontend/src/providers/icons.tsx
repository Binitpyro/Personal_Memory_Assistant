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

interface ProviderIconProps {
  id: string;
  className?: string;
}

export const ProviderIcon: React.FC<ProviderIconProps> = ({ id, className = 'w-5 h-5' }) => {
  switch (id) {
    case 'gemini':
      return <Sparkles className={`${className} text-indigo-400`} />;
    case 'openai':
      return <Cpu className={`${className} text-emerald-400`} />;
    case 'anthropic':
      return <Fingerprint className={`${className} text-orange-400`} />;
    case 'groq':
      return <Zap className={`${className} text-amber-400`} />;
    case 'openrouter':
      return <Globe className={`${className} text-sky-400`} />;
    case 'nvidia_nim':
      return <CircleDot className={`${className} text-green-500`} />;
    case 'ollama':
      return <Terminal className={`${className} text-zinc-400`} />;
    case 'lm_studio':
      return <Monitor className={`${className} text-blue-400`} />;
    case 'openai_compatible':
    default:
      return <Settings className={`${className} text-slate-400`} />;
  }
};
