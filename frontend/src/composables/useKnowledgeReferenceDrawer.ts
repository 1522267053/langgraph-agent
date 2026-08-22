import { ref, shallowRef } from 'vue'
import type { KnowledgeReference } from '@/types/knowledge'

const visible = ref(false)
const reference = shallowRef<KnowledgeReference | null>(null)

export function useKnowledgeReferenceDrawer() {
  function open(selectedReference: KnowledgeReference): void {
    reference.value = selectedReference
    visible.value = true
  }

  function close(): void {
    visible.value = false
  }

  return { visible, reference, open, close }
}
