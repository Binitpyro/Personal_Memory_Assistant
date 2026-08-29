/**
 * Extracted from SettingsPage.tsx, which had grown to 1341 lines holding ten
 * unrelated section components in one unbroken scroll. Behaviour is unchanged;
 * only the file boundary moved.
 */
import { toast } from 'sonner'

/**
 * Confirmation via sonner, matching the pattern SearchPage established. These
 * actions download hundreds of megabytes, delete a venv and its model weights,
 * or drop cached work - none of them should be one click.
 */
export function confirmThen(
  message: string,
  description: string,
  label: string,
  run: () => void | Promise<void>,
) {
  toast(message, {
    description,
    action: { label, onClick: () => void run() },
    cancel: { label: 'Cancel', onClick: () => {} },
  })
}
