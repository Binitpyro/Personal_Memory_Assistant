import { FileQuestion } from 'lucide-react'
import { Link } from 'react-router-dom'

/**
 * There was no `*` route, so an unknown path rendered a blank page inside the
 * shell - no message, no way back except the nav.
 */
export function NotFoundPage() {
    return (
        <div className="flex-1 flex flex-col items-center justify-center gap-4 p-8 text-center">
            <div className="w-14 h-14 rounded-full bg-primary/10 text-primary flex items-center justify-center">
                <FileQuestion className="w-7 h-7" />
            </div>
            <h2 className="text-xl font-bold">This page does not exist</h2>
            <p className="text-sm text-text-secondary max-w-sm">
                The address you followed is not part of PMA. Your library and settings are
                unaffected.
            </p>
            <Link
                to="/library"
                className="glass-button !bg-plate !text-on-plate px-6 py-2.5 font-semibold mt-2"
            >
                Back to Library
            </Link>
        </div>
    )
}
