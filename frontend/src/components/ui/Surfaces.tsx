import type { ReactNode, HTMLAttributes } from 'react';

/**
 * The structural half of the Specimen Cabinet.
 *
 * The inversion these exist to express: in a typical dark app the background is
 * the darkest thing and cards float lighter above it. Here the CASE is the
 * lighter material and compartments are dark recesses cut into it. Depth runs
 * into the screen, not out of it — which is what stops the app reading as a
 * generic dark UI regardless of palette.
 *
 * Structural, never textural: no wood grain, no bevelled metal, no rendered
 * pulls. Those date on sight.
 */

/** A compartment cut into the case. The default container for content. */
export function Well({
  className = '',
  children,
  ...rest
}: Readonly<HTMLAttributes<HTMLDivElement>>) {
  return (
    <div className={`well ${className}`} {...rest}>
      {children}
    </div>
  );
}

/** A flush panel that sits ON the case rather than being cut into it. */
export function Panel({
  className = '',
  children,
  ...rest
}: Readonly<HTMLAttributes<HTMLDivElement>>) {
  return (
    <div className={`bg-surface border border-rule rounded-xl ${className}`} {...rest}>
      {children}
    </div>
  );
}

/** A mono catalogue line over a serif name — the drawer's label slip. */
export function LabelSlip({
  mark,
  name,
  extent,
  className = '',
}: Readonly<{ mark: string; name: string; extent?: ReactNode; className?: string }>) {
  return (
    <div className={`flex items-center gap-3 min-w-0 ${className}`}>
      <div className="flex-grow min-w-0">
        <div className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary">{mark}</div>
        <div className="font-serif text-base leading-tight truncate">{name}</div>
      </div>
      {extent !== undefined && (
        <div className="font-mono text-[10px] text-text-tertiary text-right leading-snug shrink-0">{extent}</div>
      )}
    </div>
  );
}

/**
 * One face in the catalogue index, replacing the icon rail.
 *
 * `open` is a POSITION, not a highlight: the active drawer takes the surface
 * tone, gains brass on its leading edge and casts a shadow into the well. No
 * pill, no tint, no left-border accent — those are the generic-app vocabulary.
 */
export function DrawerFront({
  open = false,
  className = '',
  children,
  ...rest
}: Readonly<HTMLAttributes<HTMLDivElement> & { open?: boolean }>) {
  return (
    <div
      className={
        'px-3 py-2.5 border-t border-t-white/[0.06] border-b border-b-black/40 transition-colors duration-120 ' +
        (open
          ? 'bg-surface shadow-[inset_3px_0_0_var(--color-plate),5px_0_12px_rgba(0,0,0,.45)] '
          : 'bg-raised hover:bg-surface ') +
        className
      }
      {...rest}
    >
      <div className="flex items-center gap-3">
        {/* The pull is a rule, not a knob. It reads as a handle by position and
            material without rendering a physical object. */}
        <span
          aria-hidden
          className={`w-5 h-[3px] rounded-[1px] bg-plate shrink-0 ${open ? 'opacity-100' : 'opacity-70'}`}
        />
        <div className="flex-grow min-w-0">{children}</div>
      </div>
    </div>
  );
}

/** One row of a specimen card's ruled label. */
export function Field({
  label,
  children,
}: Readonly<{ label: string; children: ReactNode }>) {
  return (
    <div className="grid grid-cols-[5.5rem_1fr] border-t border-rule">
      <div className="py-1.5 font-mono text-[9px] tracking-[0.14em] uppercase text-text-tertiary">{label}</div>
      <div className="py-1.5 text-right text-xs text-text-secondary min-w-0 truncate">{children}</div>
    </div>
  );
}

/**
 * A mounted specimen: name, kind, then a ruled field block in fixed positions
 * so the eye reads down a column instead of scanning prose.
 *
 * Fields come from `FileEntry` — path, size, type, usage_count — and nothing
 * else, because that is the entire per-file surface the API exposes. There is
 * no indexed date, no per-file chunk count and no catalogue number, so this
 * does not pretend to have them.
 */
export function SpecimenCard({
  name,
  kind,
  className = '',
  children,
}: Readonly<{ name: string; kind?: string; className?: string; children?: ReactNode }>) {
  return (
    <div className={`bg-surface border border-edge rounded-md p-4 ${className}`}>
      <div className="flex items-baseline justify-between gap-3">
        <div className="font-serif text-base leading-tight truncate">{name}</div>
        {kind && <div className="font-mono text-[10px] text-primary shrink-0">{kind}</div>}
      </div>
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}
