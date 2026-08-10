interface HashFileMessage {
    readonly file: File
    readonly part_size: number
    readonly type: 'hash-file'
}

interface HashPartMessage {
    readonly part: Blob
    readonly type: 'hash-part'
}

interface WorkerScope {
    onmessage:
        | ((event: MessageEvent<HashFileMessage | HashPartMessage>) => void)
        | null
    postMessage(message: unknown): void
}

const scope = self as unknown as WorkerScope

function hex(buffer: ArrayBuffer): string {
    return [...new Uint8Array(buffer)]
        .map((byte) => byte.toString(16).padStart(2, '0'))
        .join('')
}

async function digest(value: BufferSource): Promise<string> {
    return hex(await crypto.subtle.digest('SHA-256', value))
}

scope.onmessage = (event) => {
    void (async () => {
        try {
            if (event.data.type === 'hash-part') {
                scope.postMessage({
                    sha256: await digest(await event.data.part.arrayBuffer()),
                    type: 'hash-part-result',
                })
                return
            }
            const { file, part_size: partSize } = event.data
            if (
                !Number.isSafeInteger(partSize) ||
                partSize < 1 ||
                file.size < 1
            ) {
                throw new Error('Invalid hash request')
            }
            const bytes = await file.arrayBuffer()
            const partDigests: string[] = []
            for (
                let offset = 0;
                offset < bytes.byteLength;
                offset += partSize
            ) {
                const length = Math.min(partSize, bytes.byteLength - offset)
                partDigests.push(
                    await digest(new Uint8Array(bytes, offset, length)),
                )
            }
            scope.postMessage({
                part_sha256: partDigests,
                sha256: await digest(bytes),
                type: 'hash-result',
            })
        } catch {
            scope.postMessage({ type: 'hash-error' })
        }
    })()
}

export {}
