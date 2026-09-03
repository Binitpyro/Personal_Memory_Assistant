import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, ChevronRight, Check } from 'lucide-react';
import { Button } from '../components/ui';

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
  const popoverRef = useRef<HTMLDivElement>(null);

  const endTour = () => {
    setIsActive(false);
    localStorage.setItem('pma_tour_completed', 'true');
  };

  useEffect(() => {
    if (!isActive) return;

    let scrolled = false;

    const measure = (el: Element) => setTargetRect(el.getBoundingClientRect());

    const findAndMeasure = () => {
      const el = document.getElementById(steps[currentStep].targetId);
      if (!el) {
        setTargetRect(null);
        return null;
      }
      measure(el);
      if (!scrolled) {
        scrolled = true;
        // `scroll-behavior: auto` in index.css's reduced-motion block cannot
        // reach this: passing `behavior` explicitly overrides the CSS by spec,
        // so the check has to happen here.
        const reduced =
          typeof matchMedia === 'function' &&
          matchMedia('(prefers-reduced-motion: reduce)').matches;
        el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' });
      }
      return el;
    };

    // The target can mount late — a panel animating in, or a provider list still
    // loading. This used to be handled by re-reading getBoundingClientRect every
    // 500ms for the whole session, which is a forced reflow twice a second long
    // after the element has settled. A MutationObserver waits for the element
    // instead, and a ResizeObserver tracks it once it exists.
    const resizeObserver = new ResizeObserver(entries => {
      for (const e of entries) measure(e.target);
    });

    let mutationObserver: MutationObserver | null = null;

    const attach = () => {
      const el = findAndMeasure();
      if (el) {
        resizeObserver.observe(el);
        mutationObserver?.disconnect();
        mutationObserver = null;
      }
      return el;
    };

    if (!attach()) {
      mutationObserver = new MutationObserver(() => attach());
      mutationObserver.observe(document.body, { childList: true, subtree: true });
    }

    const onViewportChange = () => {
      const el = document.getElementById(steps[currentStep].targetId);
      if (el) measure(el);
    };
    window.addEventListener('resize', onViewportChange);
    // A tour popover anchored to a stale rect is worse than no popover.
    window.addEventListener('scroll', onViewportChange, true);

    return () => {
      resizeObserver.disconnect();
      mutationObserver?.disconnect();
      window.removeEventListener('resize', onViewportChange);
      window.removeEventListener('scroll', onViewportChange, true);
    };
  }, [isActive, currentStep]);

  // Move focus to the step when it changes, so the tour is operable by keyboard
  // at all. Deliberately NOT a focus trap: the whole point is to point AT a
  // control on the page, and trapping focus inside the popover would put that
  // control out of reach.
  useEffect(() => {
    if (isActive) popoverRef.current?.focus();
  }, [isActive, currentStep, targetRect]);

  useEffect(() => {
    if (!isActive) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') endTour();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isActive]);

  if (!isActive) return null;

  const step = steps[currentStep];

  const handleNext = () => {
    if (currentStep < steps.length - 1) {
      setCurrentStep(prev => prev + 1);
    } else {
      endTour();
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-50 pointer-events-none">
      {/* Dimmed background with cutout */}
      {targetRect && (
        <div 
          className="absolute inset-0 bg-black/40 transition-[clip-path] duration-300"
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
        <div className="absolute inset-0 bg-black/40" />
      )}

      {/* Popover */}
      {targetRect && (
        <div
          ref={popoverRef}
          role="dialog"
          aria-label={`Tour step ${currentStep + 1} of ${steps.length}: ${step.title}`}
          tabIndex={-1}
          className="absolute bg-surface border border-edge rounded-xl shadow-xl p-6 w-80 pointer-events-auto transition-[top,left] duration-300 flex flex-col gap-3"
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
            aria-label="Close tour"
            className="absolute top-3 right-3 p-1 hover:bg-raised rounded-sm text-text-secondary"
          >
            <X className="w-4 h-4" aria-hidden />
          </button>

          <div>
            <span className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary">
              Step {currentStep + 1} of {steps.length}
            </span>
            <h3 className="font-serif text-lg font-medium leading-tight mt-1">{step.title}</h3>
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
            <Button variant="plate" size="sm" onClick={handleNext}>
              {currentStep < steps.length - 1 ? (
                <>Next <ChevronRight className="w-4 h-4" aria-hidden /></>
              ) : (
                <>Done <Check className="w-4 h-4" aria-hidden /></>
              )}
            </Button>
          </div>
        </div>
      )}
      
      {/* If target isn't found, just show a fallback dialog */}
      {!targetRect && (
        <div
          ref={popoverRef}
          role="dialog"
          aria-label="Product tour"
          tabIndex={-1}
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-surface border border-edge rounded-xl shadow-xl p-6 w-80 pointer-events-auto flex flex-col gap-3 text-center"
        >
          <p className="text-sm text-text-secondary">Tour item not visible yet. Please configure a provider first, or skip the tour.</p>
          <Button variant="plate" size="sm" className="w-full mt-2" onClick={handleNext}>Next Step</Button>
          <button onClick={endTour} className="text-xs font-semibold text-text-secondary hover:text-text-primary w-full mt-1">Skip Tour</button>
        </div>
      )}
    </div>,
    document.body
  );
}
