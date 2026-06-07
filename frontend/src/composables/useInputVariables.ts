import type { Ref } from 'vue'
import type { FieldType } from '@/types/flow'
import type { NodeVariable } from '@/components/FlowEditor/config/types'

export function useInputVariables<T extends { input_variables: NodeVariable[] }>(
  localConfig: Ref<T>,
  updateConfig: () => void
): {
  addInputVariable: () => void
  removeInputVariable: (index: number) => void
  handleSourceTypeChange: (index: number, type: FieldType | undefined) => void
} {
  function addInputVariable(): void {
    if (!localConfig.value.input_variables) {
      localConfig.value.input_variables = []
    }
    localConfig.value.input_variables.push({
      name: '',
      source: '',
      type: 'string'
    })
    updateConfig()
  }

  function removeInputVariable(index: number): void {
    if (!localConfig.value.input_variables) return
    localConfig.value.input_variables.splice(index, 1)
    updateConfig()
  }

  function handleSourceTypeChange(index: number, type: FieldType | undefined): void {
    if (type && localConfig.value.input_variables?.[index]) {
      localConfig.value.input_variables[index].type = type
      updateConfig()
    }
  }

  return { addInputVariable, removeInputVariable, handleSourceTypeChange }
}
