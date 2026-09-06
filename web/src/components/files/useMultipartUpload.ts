import { ref } from 'vue'

import { filesApi, type FileUploadCompleted } from '../../api/files'
import { driveCopy } from '../../copy/pages/drive'
import {
    createMultipartUploader,
    createPresignedUploadTransport,
} from '../../uploads/multipart'
import type { TrayItem } from './UploadTray.vue'

export function useMultipartUpload(allowedObjectOrigin: () => string) {
    const tray = ref<TrayItem[]>([])

    async function upload(
        file: File,
        folderId: string,
    ): Promise<FileUploadCompleted | null> {
        tray.value = [
            ...tray.value,
            { name: file.name, status: driveCopy.uploading },
        ]
        try {
            const transport = createPresignedUploadTransport({
                allowedObjectOrigin: allowedObjectOrigin(),
            })
            const uploader = createMultipartUploader({
                filesApi,
                uploadPart: transport.put,
            })
            const result = await uploader.upload({ file, folder_id: folderId })
            tray.value = tray.value.map((item) =>
                item.name === file.name
                    ? { ...item, status: driveCopy.scanning }
                    : item,
            )
            return result
        } catch {
            tray.value = tray.value.filter((item) => item.name !== file.name)
            return null
        }
    }

    function clearTray(): void {
        tray.value = []
    }

    return { tray, upload, clearTray }
}
