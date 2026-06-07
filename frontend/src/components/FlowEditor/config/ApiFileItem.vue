<script lang="ts">
import { defineComponent, h, ref, onMounted } from 'vue'
import type { FileInfo } from '@/api/file'
import { fileApi } from '@/api/file'
import { formatFileSize, isImage } from '@/utils/format'

export default defineComponent({
  name: 'ApiFileItem',
  props: {
    fileId: { type: Number, required: true }
  },
  emits: ['remove'],
  setup(props, { emit }) {
    const file = ref<FileInfo | null>(null)
    const loading = ref(true)

    onMounted(async () => {
      try {
        const res = await fileApi.page({
          condition: { id: props.fileId } as Record<string, unknown>,
          page: 1,
          page_size: 1
        })
        const list = res.data?.data?.list || res.data?.data?.rows || []
        if (list.length > 0) {
          file.value = list[0]
        }
      } catch {
        // ignore
      } finally {
        loading.value = false
      }
    })

    return () => {
      if (loading.value) {
        return h('div', { class: 'file-item' }, [
          h('span', { class: 'file-name' }, `加载中... (ID: ${props.fileId})`)
        ])
      }
      if (!file.value) {
        return h('div', { class: 'file-item' }, [
          h('span', { class: 'file-name' }, `文件已删除 (ID: ${props.fileId})`),
          h(
            'button',
            {
              class: 'file-remove-btn',
              onClick: () => emit('remove', props.fileId)
            },
            '删除'
          )
        ])
      }
      const f = file.value
      return h('div', { class: 'file-item' }, [
        isImage(f.mime_type)
          ? h('img', {
              class: 'file-thumbnail',
              src: f.preview_url || fileApi.download(f.id)
            })
          : h('div', { class: 'file-icon' }, f.file_type?.toUpperCase() || 'FILE'),
        h('div', { class: 'file-info' }, [
          h('span', { class: 'file-name', title: f.original_name }, f.original_name),
          h('span', { class: 'file-size' }, formatFileSize(f.file_size))
        ]),
        h(
          'button',
          {
            class: 'file-remove-btn',
            onClick: () => emit('remove', props.fileId)
          },
          '删除'
        )
      ])
    }
  }
})
</script>
