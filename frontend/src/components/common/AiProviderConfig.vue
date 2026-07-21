<script setup lang="ts">
import { computed, onMounted, ref, watch, nextTick } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { aiProviderApi, type ProviderInfo, type ModelInfo } from '@/api/ai_provider'
import type { ModelCapabilities } from '@/components/FlowEditor/config/types'
import { CONTEXT_LENGTH_PRESETS, parseContextLength } from '@/components/FlowEditor/config/types'

const props = withDefaults(
  defineProps<{
    showCapabilities?: boolean
    showContextLength?: boolean
    showMaxTokens?: boolean
    showTemperature?: boolean
    showReasoningEffort?: boolean
    showExtraBody?: boolean
    resetOnProviderChange?: boolean
    disabled?: boolean
    apiKeyPlaceholder?: string
    providerClearable?: boolean
    modelClearable?: boolean
    autoSelectFirst?: boolean
    labelPosition?: 'left' | 'top'
  }>(),
  {
    showCapabilities: false,
    showContextLength: false,
    showMaxTokens: false,
    showTemperature: false,
    showReasoningEffort: false,
    showExtraBody: false,
    resetOnProviderChange: true,
    disabled: false,
    apiKeyPlaceholder: '请输入 API Key',
    providerClearable: false,
    modelClearable: false,
    autoSelectFirst: false,
    labelPosition: 'left'
  }
)

const emit = defineEmits<{
  (e: 'change'): void
}>()

const provider = defineModel<string>('provider', { default: '' })
const model = defineModel<string>('model', { default: '' })
const apiKey = defineModel<string>('apiKey', { default: '' })
const baseUrl = defineModel<string>('baseUrl', { default: '' })
const contextLength = defineModel<number | undefined>('contextLength')
const capabilities = defineModel<ModelCapabilities>('capabilities')
const maxTokens = defineModel<number | undefined>('maxTokens')
const temperature = defineModel<number | undefined>('temperature')
const reasoningEffort = defineModel<string | undefined>('reasoningEffort')
const extraBody = defineModel<Record<string, unknown> | undefined>('extraBody')

const _init = ref(true)
const providerList = ref<ProviderInfo[]>([])
const modelList = ref<
  {
    value: string
    label: string
    capabilities?: ModelCapabilities
    context_length?: number
    max_tokens?: number
  }[]
>([])

const selectedModelOption = computed(() => modelList.value.find(m => m.value === model.value))

function getProviderBaseUrl(name: string): string {
  return providerList.value.find(p => p.name === name)?.default_base_url || ''
}

function hasAnyCapability(): boolean {
  const caps = capabilities.value
  if (!caps) return false
  return caps.image || caps.video || caps.audio || caps.pdf || caps.xlsx
}

function applyModelAutoFill() {
  const opt = selectedModelOption.value
  if (!opt) return
  if (capabilities.value && opt.capabilities && !hasAnyCapability()) {
    capabilities.value = { ...opt.capabilities }
  }
  if (opt.context_length && !contextLength.value) {
    contextLength.value = opt.context_length
  }
  if (opt.max_tokens && maxTokens.value !== undefined && !maxTokens.value) {
    maxTokens.value = opt.max_tokens
  }
}

function onContextLengthChange(val: string | number | undefined) {
  if (val !== undefined && val !== null && val !== '') {
    const parsed = parseContextLength(val)
    if (parsed === undefined) {
      ElMessage.error('上下文窗口格式无效，请输入数字或带单位（如 32000、32K、1M）')
      nextTick(() => {
        contextLength.value = undefined
      })
      return
    }
    contextLength.value = parsed
  } else {
    contextLength.value = undefined
  }
  emit('change')
}

async function loadProviders() {
  try {
    const res = await aiProviderApi.list()
    providerList.value = res.data.data || []
  } catch {
    /* silent */
  }
}

async function loadModels(providerId: string) {
  if (!providerId) {
    modelList.value = []
    return
  }
  try {
    const res = await aiProviderApi.getModels(providerId)
    const models = res.data.data || []
    modelList.value = models.map((m: ModelInfo) => {
      const inputModalities = m.modalities?.input || []
      return {
        value: m.model_id,
        label: m.name,
        capabilities: {
          image: inputModalities.includes('image'),
          video: inputModalities.includes('video'),
          audio: inputModalities.includes('audio'),
          pdf: false,
          xlsx: false
        },
        context_length: m.limits?.context,
        max_tokens: m.limits?.output
      }
    })
  } catch {
    modelList.value = []
  }
}

async function onProviderChange() {
  if (_init.value) return
  if (props.resetOnProviderChange) {
    model.value = ''
    if (capabilities.value) {
      capabilities.value = { image: false, video: false, audio: false, pdf: false, xlsx: false }
    }
    contextLength.value = undefined
    if (maxTokens.value !== undefined) maxTokens.value = undefined
  }
  const defaultUrl = getProviderBaseUrl(provider.value)
  if (defaultUrl && !baseUrl.value) {
    baseUrl.value = defaultUrl
  }
  await loadModels(provider.value)
  applyModelAutoFill()
  emit('change')
}

function onModelChange() {
  if (_init.value) return
  applyModelAutoFill()
  emit('change')
}

watch(provider, onProviderChange, { flush: 'sync' })
watch(model, onModelChange, { flush: 'sync' })

onMounted(async () => {
  await loadProviders()
  if (props.autoSelectFirst && !provider.value && providerList.value.length > 0) {
    provider.value = providerList.value[0].name
  }
  if (provider.value) {
    await loadModels(provider.value)
    applyModelAutoFill()
  }
  _init.value = false
})

function onFieldChange() {
  emit('change')
}

const extraBodyText = ref('')

watch(
  () => extraBody.value,
  eb => {
    extraBodyText.value = eb && Object.keys(eb).length > 0 ? JSON.stringify(eb, null, 2) : ''
  },
  { immediate: true }
)

function handleExtraBodyBlur() {
  const text = extraBodyText.value.trim()
  if (!text) {
    extraBody.value = {}
  } else {
    try {
      extraBody.value = JSON.parse(text)
    } catch {
      return
    }
  }
  emit('change')
}
</script>

<template>
  <el-form v-if="labelPosition === 'left'" label-width="90px" size="small">
    <el-form-item label="供应商">
      <el-select
        v-model="provider"
        placeholder="选择供应商"
        style="width: 100%"
        filterable
        :clearable="providerClearable"
        :disabled="disabled"
        @change="onFieldChange"
      >
        <el-option
          v-for="item in providerList"
          :key="item.name"
          :label="item.label"
          :value="item.name"
        />
      </el-select>
    </el-form-item>
    <el-form-item label="模型">
      <el-select
        v-model="model"
        placeholder="选择或输入模型名称"
        style="width: 100%"
        :disabled="disabled || !provider"
        filterable
        allow-create
        default-first-option
        :clearable="modelClearable"
        @change="onFieldChange"
      >
        <el-option
          v-for="item in modelList"
          :key="item.value"
          :label="item.label"
          :value="item.value"
        />
      </el-select>
    </el-form-item>
    <el-form-item v-if="showCapabilities" label="支持内容">
      <el-checkbox v-model="capabilities.image" @change="onFieldChange">图片</el-checkbox>
      <el-checkbox v-model="capabilities.video" @change="onFieldChange">视频</el-checkbox>
      <el-checkbox v-model="capabilities.audio" @change="onFieldChange">音频</el-checkbox>
      <el-checkbox v-model="capabilities.pdf" @change="onFieldChange">PDF</el-checkbox>
      <el-checkbox v-model="capabilities.xlsx" @change="onFieldChange">Excel</el-checkbox>
    </el-form-item>
    <el-form-item label="API Key">
      <el-input
        v-model="apiKey"
        type="password"
        :placeholder="apiKeyPlaceholder"
        show-password
        :disabled="disabled"
        @blur="onFieldChange"
      />
    </el-form-item>
    <el-form-item label="Base URL">
      <el-input
        v-model="baseUrl"
        placeholder="可选，自定义接口地址"
        :disabled="disabled"
        @blur="onFieldChange"
      />
    </el-form-item>
    <el-form-item v-if="showContextLength">
      <template #label>
        上下文窗口
        <el-tooltip
          content="用于上下文自动压缩，当对话占用超过 80% 时自动压缩旧消息。不填则不会自动压缩。"
        >
          <el-icon class="context-tip-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
      </template>
      <el-select
        v-model="contextLength"
        placeholder="选择或输入上下文大小"
        style="width: calc(100% - 80px)"
        filterable
        allow-create
        default-first-option
        clearable
        :disabled="disabled"
        @change="onContextLengthChange"
      >
        <el-option
          v-for="item in CONTEXT_LENGTH_PRESETS"
          :key="item.value"
          :label="item.label"
          :value="item.value"
        />
      </el-select>
      <span class="context-length-unit" style="margin-left: 8px">tokens</span>
    </el-form-item>
    <el-form-item v-if="showMaxTokens" label="Max Tokens">
      <el-input-number
        v-model="maxTokens"
        :min="256"
        :max="128000"
        :step="1024"
        placeholder="最大输出 token 数"
        :disabled="disabled"
        @change="onFieldChange"
      />
    </el-form-item>
    <el-form-item v-if="showTemperature" label="温度">
      <el-input-number
        v-model="temperature"
        :min="0"
        :max="2"
        :step="0.1"
        :precision="1"
        controls-position="right"
        :disabled="disabled"
        @change="onFieldChange"
      />
    </el-form-item>
    <el-form-item v-if="showReasoningEffort" label="推理深度">
      <el-select
        v-model="reasoningEffort"
        placeholder="不设置（使用模型默认）"
        style="width: 100%"
        clearable
        filterable
        allow-create
        default-first-option
        :disabled="disabled"
        @change="onFieldChange"
      >
        <el-option label="low" value="low" />
        <el-option label="medium" value="medium" />
        <el-option label="high" value="high" />
      </el-select>
    </el-form-item>
    <el-form-item v-if="showExtraBody" label="附加参数">
      <el-input
        v-model="extraBodyText"
        type="textarea"
        :rows="2"
        placeholder='JSON 格式，如: {"enable_search": true}'
        :disabled="disabled"
        @blur="handleExtraBodyBlur"
      />
    </el-form-item>
  </el-form>

  <template v-else>
    <el-form-item label="AI 供应商">
      <el-select
        v-model="provider"
        placeholder="请选择供应商"
        style="width: 100%"
        filterable
        :clearable="providerClearable"
        :disabled="disabled"
        @change="onFieldChange"
      >
        <el-option
          v-for="item in providerList"
          :key="item.name"
          :label="item.label"
          :value="item.name"
        />
      </el-select>
    </el-form-item>
    <el-form-item label="API Key">
      <el-input
        v-model="apiKey"
        type="password"
        :placeholder="apiKeyPlaceholder"
        show-password
        :disabled="disabled"
        @blur="onFieldChange"
      />
    </el-form-item>
    <el-form-item label="模型">
      <el-select
        v-model="model"
        placeholder="选择或输入模型名称"
        style="width: 100%"
        :disabled="disabled || !provider"
        filterable
        allow-create
        default-first-option
        :clearable="modelClearable"
        @change="onFieldChange"
      >
        <el-option
          v-for="item in modelList"
          :key="item.value"
          :label="item.label"
          :value="item.value"
        />
      </el-select>
    </el-form-item>
    <el-form-item label="Base URL">
      <el-input
        v-model="baseUrl"
        placeholder="可选，自定义接口地址"
        :disabled="disabled"
        @blur="onFieldChange"
      />
    </el-form-item>
    <el-form-item v-if="showContextLength">
      <template #label>
        上下文窗口
        <el-tooltip
          content="用于上下文自动压缩，当对话占用超过 80% 时自动压缩旧消息。不填则不会自动压缩。"
        >
          <el-icon class="context-tip-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
      </template>
      <el-select
        v-model="contextLength"
        placeholder="选择或输入上下文大小"
        style="width: calc(100% - 80px)"
        filterable
        allow-create
        default-first-option
        clearable
        :disabled="disabled"
        @change="onContextLengthChange"
      >
        <el-option
          v-for="item in CONTEXT_LENGTH_PRESETS"
          :key="item.value"
          :label="item.label"
          :value="item.value"
        />
      </el-select>
      <span class="context-length-unit" style="margin-left: 8px">tokens</span>
    </el-form-item>
    <el-form-item v-if="showMaxTokens" label="Max Tokens">
      <el-input-number
        v-model="maxTokens"
        :min="256"
        :max="128000"
        :step="1024"
        placeholder="最大输出 token 数"
        :disabled="disabled"
        @change="onFieldChange"
      />
    </el-form-item>
    <el-form-item v-if="showTemperature" label="温度">
      <el-input-number
        v-model="temperature"
        :min="0"
        :max="2"
        :step="0.1"
        :precision="1"
        controls-position="right"
        :disabled="disabled"
        @change="onFieldChange"
      />
    </el-form-item>
    <el-form-item v-if="showReasoningEffort" label="推理深度">
      <el-select
        v-model="reasoningEffort"
        placeholder="不设置（使用模型默认）"
        style="width: 100%"
        clearable
        filterable
        allow-create
        default-first-option
        :disabled="disabled"
        @change="onFieldChange"
      >
        <el-option label="low" value="low" />
        <el-option label="medium" value="medium" />
        <el-option label="high" value="high" />
      </el-select>
    </el-form-item>
    <el-form-item v-if="showExtraBody" label="附加参数">
      <el-input
        v-model="extraBodyText"
        type="textarea"
        :rows="2"
        placeholder='JSON 格式，如: {"enable_search": true}'
        :disabled="disabled"
        @blur="handleExtraBodyBlur"
      />
    </el-form-item>
  </template>
</template>

<style scoped>
.context-length-unit {
  font-size: 12px;
  color: #909399;
}
.context-tip-icon {
  margin-left: 4px;
  cursor: help;
  color: #909399;
}
</style>
