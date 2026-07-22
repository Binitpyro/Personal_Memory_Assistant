import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';

interface StreamActivityContextType {
  isStreamActive: boolean;
  setIsStreamActive: (active: boolean) => void;
}

const StreamActivityContext = createContext<StreamActivityContextType>({
  isStreamActive: false,
  setIsStreamActive: () => {},
});

export function StreamActivityProvider({ children }: { children: ReactNode }) {
  const [isStreamActive, setIsStreamActive] = useState(false);
  return (
    <StreamActivityContext.Provider value={{ isStreamActive, setIsStreamActive }}>
      {children}
    </StreamActivityContext.Provider>
  );
}

export function useStreamActivity() {
  return useContext(StreamActivityContext);
}
