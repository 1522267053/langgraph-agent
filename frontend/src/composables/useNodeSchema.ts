import { ref, type Ref } from 'vue'
import type { NodeVariable } from '@/types/flow'
import { nodeSchemaApi, type NodeConfigSchema, type ConfigField } from '@/api/nodeSchema'

const schemas: Ref<Record<string, NodeConfigSchema>> = ref({})
const loaded = ref(false)
const loading = ref(false)
let _loading: Promise<void> | null = null

function _extractFieldDefault(field: ConfigField): unknown {
  if (field.default !== undefined) return field.default
  if (field.type === 'array') return []
  if (field.type === 'number') return undefined
  return ''
}

function _extractVariableField(field: ConfigField): NodeVariable[] {
  const def = field.default
  if (Array.isArray(def)) {
    return def.map((v: Record<string, unknown>) => ({
      name: (v.name as string) || '',
      source: (v.source as string) || '',
      type: (v.type as string) || undefined
    }))
  }
  return []
}

async function load(): Promise<void> {
  if (_loading) return _loading
  if (loaded.value) return
  loading.value = true
  _loading = (async () => {
    try {
      const res = await nodeSchemaApi.getAll()
      if (res.data.code === 1 && res.data.data) {
        schemas.value = res.data.data
      }
    } catch {
      // API 不可用时回退
    }
    loaded.value = true
    loading.value = false
  })()
  return _loading
}

export function useNodeSchema() {
  function getSchema(nodeType: string): NodeConfigSchema | undefined {
    return schemas.value[nodeType]
  }

  function getConfigField(nodeType: string, fieldName: string): ConfigField | undefined {
    return getSchema(nodeType)?.config_fields.find(f => f.name === fieldName)
  }

  function getOutputVariables(nodeType: string): NodeVariable[] {
    const field = getConfigField(nodeType, 'output_variables')
    if (field) return _extractVariableField(field)
    return []
  }

  function getInputVariables(nodeType: string): NodeVariable[] {
    const field = getConfigField(nodeType, 'input_variables')
    if (field) return _extractVariableField(field)
    return []
  }

  function getDefaultConfig(nodeType: string): Record<string, unknown> {
    const schema = getSchema(nodeType)
    if (!schema) return {}
    const config: Record<string, unknown> = {}
    for (const field of schema.config_fields) {
      const def = _extractFieldDefault(field)
      if (def !== undefined) {
        config[field.name] = def
      }
    }
    return config
  }

  function isLoaded(): boolean {
    return loaded.value
  }

  return {
    schemas,
    loaded,
    loading,
    load,
    getSchema,
    getConfigField,
    getOutputVariables,
    getInputVariables,
    getDefaultConfig,
    isLoaded
  }
}
