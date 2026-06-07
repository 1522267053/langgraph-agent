import { ref, toRaw, watch, type Ref } from 'vue'

function deepClone<T>(obj: T): T {
  return JSON.parse(JSON.stringify(toRaw(obj)))
}

export function useConfigBase<T extends object>(
  config: () => T,
  emit: (e: 'update:config', value: T) => void,
  options?: { onBeforeUpdate?: () => void }
): {
  localConfig: Ref<T>
  updateConfig: () => void
} {
  const localConfig = ref<T>(config()) as Ref<T>

  watch(
    config,
    newConfig => {
      options?.onBeforeUpdate?.()
      localConfig.value = deepClone(newConfig)
    },
    { deep: true, immediate: true }
  )

  function updateConfig(): void {
    emit('update:config', deepClone(localConfig.value))
  }

  return { localConfig, updateConfig }
}
