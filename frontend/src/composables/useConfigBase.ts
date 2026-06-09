import { nextTick, ref, toRaw, watch, type Ref } from 'vue'

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
  let _updating = false

  watch(
    config,
    newConfig => {
      if (_updating) return
      options?.onBeforeUpdate?.()
      localConfig.value = deepClone(newConfig)
    },
    { deep: true, immediate: true }
  )

  function updateConfig(): void {
    _updating = true
    emit('update:config', deepClone(localConfig.value))
    nextTick(() => {
      _updating = false
    })
  }

  return { localConfig, updateConfig }
}
