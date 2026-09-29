import { Link } from 'react-router-dom'
import { buttonClasses } from '../components/ui'

/**
 * There was no `*` route, so an unknown path rendered a blank page inside the
 * shell - no message, no way back except the nav.
 */
export function NotFoundPage() {
    return (
        <div className="flex-1 flex flex-col items-center justify-center gap-4 p-8 text-center">
            <h2 className="stock text-[40px] leading-[.92] m-0">This page does not exist</h2>
            <p className="text-sm text-text-secondary max-w-sm">
                The address you followed is not part of PMA. Your library and settings are
                unaffected.
            </p>
            <Link
                to="/library"
                className={buttonClasses({ variant: 'plate', className: 'mt-2' })}
            >
                Back to Library
            </Link>
        </div>
    )
}
