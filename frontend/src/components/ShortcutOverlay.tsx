/**
 * ShortcutOverlay.tsx
 *
 * The `?` / F1 key reference, rendered from `interaction/keymap.ts` so it
 * cannot drift from the handlers — a help screen maintained separately from
 * its bindings is wrong within one release.
 *
 * Native <dialog> + showModal(), matching ModelPicker: the platform supplies
 * the focus trap, Escape, the inert background and the top layer.
 */

import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { bindingsFor, GROUP_TITLE, type KeyGroup } from '../interaction/keymap';

export interface ShortcutOverlayProps {
    readonly open: boolean;
    readonly onClose: () => void;
    /**
     * Which halves of the keymap apply. The 2D treemap has no camera, so it
     * passes ['outliner'] — a reference card listing keys that do nothing is
     * worse than one that is short.
     */
    readonly groups: readonly KeyGroup[];
    readonly title: string;
}

export function ShortcutOverlay({ open, onClose, groups, title }: ShortcutOverlayProps) {
    const dialogRef = useRef<HTMLDialogElement>(null);

    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        if (open && !dialog.open) dialog.showModal();
        else if (!open && dialog.open) dialog.close();
    }, [open]);

    // A NATIVE listener. The dialog `close` event does not bubble, and Escape
    // closes through the user agent rather than through React — without this
    // the parent's `open` stays true and the overlay cannot be reopened. Same
    // reasoning as ModelPicker; see its test for the contract.
    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        const onNativeClose = () => onClose();
        dialog.addEventListener('close', onNativeClose);
        return () => dialog.removeEventListener('close', onNativeClose);
    }, [onClose]);

    const shown = bindingsFor(groups);
    const visibleGroups = groups.filter(g => shown.some(b => b.group === g));

    return (
        <dialog
            ref={dialogRef}
            aria-label={title}
            className="w-full max-w-xl bg-surface text-text-primary border border-edge rounded-xl shadow-2xl p-0"
        >
            <div className="flex items-center justify-between px-6 py-4 border-b border-rule">
                <h2 className="font-serif text-lg font-normal m-0">{title}</h2>
                <button
                    type="button"
                    onClick={onClose}
                    aria-label="Close the keyboard reference"
                    className="p-1 rounded-sm text-text-secondary hover:text-text-primary hover:bg-raised transition-colors"
                >
                    <X className="w-4 h-4" aria-hidden />
                </button>
            </div>

            <div className="px-6 py-5 flex flex-col gap-6 max-h-[70vh] overflow-y-auto overscroll-contain">
                {visibleGroups.map(group => (
                    <section key={group}>
                        <h3 className="font-mono text-[10px] tracking-[0.16em] uppercase text-text-tertiary m-0 mb-3">
                            {GROUP_TITLE[group]}
                        </h3>
                        <dl className="m-0 flex flex-col gap-2">
                            {shown
                                .filter(b => b.group === group)
                                .map(b => (
                                    <div key={b.keys.join('+') + b.label} className="flex items-baseline gap-3">
                                        <dt className="flex items-center gap-1 shrink-0 w-40">
                                            {b.keys.map(k => (
                                                <kbd
                                                    key={k}
                                                    className="px-1.5 py-0.5 bg-raised border border-rule rounded-xs font-mono text-[10px]"
                                                >
                                                    {k}
                                                </kbd>
                                            ))}
                                        </dt>
                                        <dd className="m-0 text-sm text-text-secondary">
                                            {b.label}
                                            {b.reference && (
                                                <span className="text-text-tertiary text-xs"> · {b.reference}</span>
                                            )}
                                        </dd>
                                    </div>
                                ))}
                        </dl>
                    </section>
                ))}
            </div>
        </dialog>
    );
}
