import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, ChevronRight, Check } from 'lucide-react';

interface TourStep {
  targetId: string;
  title: string;
  description: string;
  position: 'right' | 'left' | 'top' | 'bottom';
}

const steps: TourStep[] = [
  {
    targetId: 'tour-providers-list',
    title: 'Select a Provider',
    description: 'Pick an AI provider from the list to configure. Local models and cloud APIs are both supported.',
    position: 'right'
  },
  {
    targetId: 'tour-connection-details',
    title: 'Configure Connection',
    description: 'Enter your API key or endpoint URL. We automatically validate keys when you save.',
    position: 'left'
  },
  {
    targetId: 'tour-model-selection',
    title: 'Choose Default Model',
    description: 'After saving a valid key, you can pick the default model to use for chat and completion.',
    position: 'left'
  },
  {
    targetId: 'tour-fallback-router',
    title: 'Backup Cascade',
    description: 'Reorder providers to act as fallbacks. If the first fails (e.g. rate limit), we automatically retry down the chain.',
    position: 'right'
  }
];

export function TourOverlay() {
  const [isActive, setIsActive] = useState(() => localStorage.getItem('pma_tour_completed') !== 'true');
  const [currentStep, setCurrentStep] = useState(0);
  const [targetRect, setTargetRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    if (!isActive) return;

    const updateRect = () => {
      const el = document.getElementById(steps[currentStep].targetId);
      if (el) {
        setTargetRect(el.getBoundingClientRect());
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      } else {
        setTargetRect(null);
      }
    };

    updateRect();
    // Sometimes elements animate in, so check a few times
    const interval = setInterval(updateRect, 500);
    window.addEventListener('resize', updateRect);

    return () => {
      clearInterval(interval);
      window.removeEventListener('resize', updateRect);
    };
  }, [isActive, currentStep]);

  if (!isActive) return null;

  const step = steps[currentStep];

  const handleNext = () => {
    if (currentStep < steps.length - 1) {
      setCurrentStep(prev => prev + 1);
    } else {
      endTour();
    }
  };

  const endTour = () => {
    setIsActive(false);
    localStorage.setItem('pma_tour_completed', 'true');
  };

  return createPortal(
    <div className="fixed inset-0 z-50 pointer-events-none">
      {/* Dimmed background with cutout */}
      {targetRect && (
        <div 
          className="absolute inset-0 bg-black/40 transition-all duration-300"
          style={{
            clipPath: `polygon(
              0% 0%, 100% 0%, 100% 100%, 0% 100%, 0% 0%,
              ${targetRect.left - 8}px ${targetRect.top - 8}px,
              ${targetRect.left - 8}px ${targetRect.bottom + 8}px,
              ${targetRect.right + 8}px ${targetRect.bottom + 8}px,
              ${targetRect.right + 8}px ${targetRect.top - 8}px,
              ${targetRect.left - 8}px ${targetRect.top - 8}px
            )`
          }}
        />
      )}
      
      {!targetRect && (
        <div className="absolute inset-0 bg-black/40 transition-all duration-300" />
      )}

      {/* Popover */}
      {targetRect && (
        <div 
          className="absolute glass bg-surface backdrop-blur-2xl border border-primary/10 rounded-3xl shadow-2xl p-6 w-80 pointer-events-auto transition-all duration-300 flex flex-col gap-3"
          style={{
            top: step.position === 'bottom' ? targetRect.bottom + 16 :
                 step.position === 'top' ? targetRect.top - 16 - 200 :
                 targetRect.top,
            left: step.position === 'right' ? targetRect.right + 16 :
                  step.position === 'left' ? targetRect.left - 16 - 320 :
                  targetRect.left,
            transform: step.position === 'top' || step.position === 'bottom' ? 'translateX(0)' : 'translateY(0)'
          }}
        >
          <button 
            onClick={endTour}
            className="absolute top-3 right-3 p-1 hover:bg-raised rounded-full text-text-secondary"
          >
            <X className="w-4 h-4" />
          </button>

          <div>
            <span className="text-[10px] font-bold text-primary uppercase tracking-wider">
              Step {currentStep + 1} of {steps.length}
            </span>
            <h3 className="font-bold text-lg leading-tight mt-1">{step.title}</h3>
          </div>
          
          <p className="text-sm text-text-secondary">
            {step.description}
          </p>

          <div className="flex items-center justify-between mt-2">
            <button 
              onClick={endTour}
              className="text-xs font-semibold text-text-secondary hover:text-text-primary"
            >
              Skip Tour
            </button>
            <button 
              onClick={handleNext}
              className="glass-button !bg-plate !text-on-plate px-5 py-2.5 rounded-xl text-xs font-bold flex items-center gap-1.5 hover:opacity-90 transition-all shadow-md shadow-primary/20"
            >
              {currentStep < steps.length - 1 ? (
                <>Next <ChevronRight className="w-4 h-4" /></>
              ) : (
                <>Done <Check className="w-4 h-4" /></>
              )}
            </button>
          </div>
        </div>
      )}
      
      {/* If target isn't found, just show a fallback dialog */}
      {!targetRect && (
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 glass bg-surface backdrop-blur-2xl border border-primary/10 rounded-3xl shadow-2xl p-6 w-80 pointer-events-auto flex flex-col gap-3 text-center">
          <p className="text-sm text-text-secondary">Tour item not visible yet. Please configure a provider first, or skip the tour.</p>
          <button onClick={handleNext} className="glass-button !bg-plate !text-on-plate px-4 py-2.5 rounded-xl text-xs font-bold w-full mt-2">Next Step</button>
          <button onClick={endTour} className="text-xs font-semibold text-text-secondary hover:text-text-primary w-full mt-1">Skip Tour</button>
        </div>
      )}
    </div>,
    document.body
  );
}
