import { ElMessage } from 'element-plus'
import { knowledgeDocumentApi } from '@/api/knowledge'

export async function downloadKnowledgeDocument(
  documentId: number,
  filename: string
): Promise<void> {
  try {
    const response = await knowledgeDocumentApi.download(documentId)
    const contentType = String(response.headers['content-type'] || response.data.type || '')
    if (contentType.includes('application/json')) {
      let message = '文件不可用'
      try {
        const payload = JSON.parse(await response.data.text()) as { msg?: string }
        message = payload.msg || message
      } catch {
        // Keep the generic error when the server did not return valid JSON.
      }
      throw new Error(message)
    }

    const objectUrl = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = filename || `knowledge-${documentId}`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  } catch (error) {
    const message = error instanceof Error ? error.message : '下载失败'
    ElMessage.error({ message: message, duration: 5000 })
  }
}
