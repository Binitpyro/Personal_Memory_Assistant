import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Loader2 } from 'lucide-react';

/**
 * The one control that rises off the surface.
 *
 * Depth rule for the whole system: the case is recessed, the brass is proud.
 * Everything else insets; only `plate` lifts, which is why there should be
 * exactly one per screen.
 *
 * `plate` is a FILL and never a text colour — #B08D57 measures 3.84 against the
 * control edge, so the label inverts to ink instead (5.70 cabinet, 5.46 paper).
 * `danger` inverts the same way against oxblood.
 */
export type ButtonVariant = 'plate' | 'secondary' | 'quiet' | 'danger';
export type ButtonSize = 'sm' | 'md';

const BASE =
  'inline-flex items-center justify-center gap-2 font-medium whitespace-nowrap ' +
  'rounded-md border transition-[background-color,border-color,box-shadow,transform] duration-120 ' +
  'disabled:cursor-not-allowed disabled:shadow-none disabled:translate-y-0';

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-xs',
  md: 'h-10 px-4 text-sm',
};

const VARIANTS: Record<ButtonVariant, string> = {
  // Seats into its recess when pressed rather than scaling.
  plate:
    'bg-plate text-on-plate border-black/20 shadow-md ' +
    'hover:brightness-110 active:shadow-[inset_0_1px_3px_rgba(0,0,0,.45)] active:translate-y-px ' +
    'disabled:bg-raised disabled:text-text-tertiary disabled:border-rule',
  secondary:
    'bg-surface text-text-primary border-edge ' +
    'hover:bg-raised hover:shadow-sm active:shadow-[inset_0_1px_3px_rgba(0,0,0,.35)] active:translate-y-px ' +
    'disabled:text-text-tertiary disabled:border-rule',
  quiet:
    'bg-transparent text-text-secondary border-transparent ' +
    'hover:bg-surface hover:text-text-primary active:bg-raised active:translate-y-px ' +
    'disabled:text-text-tertiary',
  // Outlined at rest so the destructive action never looks like the default.
  danger:
    'bg-transparent text-error border-error ' +
    'hover:bg-danger-fill hover:text-on-danger active:translate-y-px ' +
    'disabled:text-text-tertiary disabled:border-rule',
};

/**
 * The same clothes, for a control that must be a link.
 *
 * A button that calls `navigate()` loses Cmd-click, middle-click and the status
 * bar, so those become `<Link>` — but a `<Link>` wrapped around a `<Button>`
 * would nest one interactive element in another. This lets the link wear the
 * button's appearance directly, which is cheaper than making `Button`
 * polymorphic for the three call sites that need it.
 */
export function buttonClasses({
  variant = 'secondary',
  size = 'md',
  className = '',
}: Readonly<{ variant?: ButtonVariant; size?: ButtonSize; className?: string }> = {}) {
  return `${BASE} ${SIZES[size]} ${VARIANTS[variant]} ${className}`;
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  readonly variant?: ButtonVariant;
  readonly size?: ButtonSize;
  readonly loading?: boolean;
  readonly icon?: ReactNode;
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  icon,
  disabled,
  className = '',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      // A loading control is not a target: it already accepted the click.
      //
      // `||`, not `??`. With `??` an explicit `disabled={false}` won the whole
      // expression and `loading` was ignored, so a control with both props -
      // the normal shape for a form submit, `disabled={!valid} loading={saving}`
      // - stayed clickable for the entire request and double-submitted. The
      // bug was invisible while LibraryPage was the only consumer, because it
      // never passed `loading`.
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={buttonClasses({ variant, size, className })}
      {...rest}
    >
      {loading ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}
