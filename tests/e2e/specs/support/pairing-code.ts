import type { Locator } from '@playwright/test'

export const PAIRING_CODE_REDACTED = '[PAIRING CODE REDACTED]'

export async function consumePairingCode(locator: Locator): Promise<string> {
    const rawCode = await locator.evaluate((element, redacted) => {
        const code = element.textContent?.trim() ?? ''
        element.textContent = redacted
        element.setAttribute('aria-hidden', 'true')
        element.setAttribute('hidden', '')
        return code
    }, PAIRING_CODE_REDACTED)
    if (rawCode === '') throw new Error('Pairing code was empty before redaction.')
    return rawCode
}
