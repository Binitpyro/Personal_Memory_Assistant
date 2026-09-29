import type { ButtonHTMLAttributes, ReactNode } from 'react';

/**
 * Safelight's button: square, a verb in caps at Archivo width 80.
 *
 * `plate` is the lamp — the one live thing, so there should be exactly one per
 * screen. Its label is onsafe (5.39 dark, 5.35 light); ink on the lamp would be
 * 3.09. `secondary` is outlined in the control edge (ink3, >= 4.92 on every
 * ground) rather than the board's line2, which measures 1.53-1.97.
 *
 * `danger` is outlined in fog at rest so a destructive action never looks like
 * the default, and fills with fog under a room-coloured label on hover.
 */
export type ButtonVariant = 'plate' | 'secondary' | 'quiet' | 'danger';
export type ButtonSize = 'sm' | 'md';

const BASE =
  'inline-flex items-center justify-center gap-2 font-semibold [font-stretch:80%] uppercase tracking-[.06em] ' +
  'leading-none whitespace-nowrap border transition-[color,border-color,filter,opacity] duration-120 ' +
  'disabled:cursor-not-allowed disabled:bg-transparent disabled:text-text-tertiary disabled:border-rule disabled:filter-none';

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-[13px]',
  md: 'h-10 px-4 text-sm',
};

const VARIANTS: Record<ButtonVariant, string> = {
  plate: 'bg-plate text-on-plate border-transparent font-bold hover:brightness-110',
  secondary: 'bg-transparent text-text-primary border-edge hover:border-text-primary',
  quiet: 'bg-transparent text-text-secondary border-transparent hover:text-text-primary',
  danger: 'bg-transparent text-error border-error hover:bg-danger-fill hover:text-on-danger',
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
      {/* A working state ends in an ellipsis rather than a spinner. */}
      {!loading && icon}
      {children}
      {loading && <span aria-hidden>…</span>}
    </button>
  );
}
