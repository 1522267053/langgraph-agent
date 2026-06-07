<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import type { MediaGenNodeConfig, MediaGenItemConfig } from './types'
import { mediaGenProviderModels, variableFormatHint, fieldTypeOptions } from './types'
import { aiProviderApi, type ProviderInfo, type MediaGenFieldDef } from '@/api/ai_provider'
import { useConfigBase } from '@/composables/useConfigBase'
import { useInputVariables } from '@/composables/useInputVariables'
import VariableSelector from '../components/VariableSelector.vue'

const props = defineProps<{
  config: MediaGenNodeConfig
  nodeId: string
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: MediaGenNodeConfig): void
}>()

const isLoadingConfig = ref(false)

const { localConfig, updateConfig } = useConfigBase(() => props.config, emit, {
  onBeforeUpdate: () => {
    isLoadingConfig.value = true
    nextTick(() => {
      isLoadingConfig.value = false
    })
  }
})

const { addInputVariable, removeInputVariable, handleSourceTypeChange } = useInputVariables(
  localConfig,
  updateConfig
)

function updateVariableSource(index: number, source: string): void {
  if (localConfig.value.input_variables[index])
    localConfig.value.input_variables[index].source = source
  updateConfig()
}

const mediaProviderList = ref<ProviderInfo[]>([])
const mediaFieldsMap = ref<Record<string, MediaGenFieldDef[]>>({})

onMounted(async () => {
  try {
    const res = await aiProviderApi.list()
    const providers = res.data.data || []
    mediaProviderList.value = providers.filter(p => {
      const caps = [p.supports_image, p.supports_audio, p.supports_video]
      return caps.some(Boolean)
    })
    for (const type of ['image', 'audio', 'video'] as const) {
      ensureTypeConfig(type)
    }
    const activeType = localConfig.value.media_type
    const typeConfig = localConfig.value[activeType] as MediaGenItemConfig
    if (typeConfig?.provider) {
      const fieldsRes = await aiProviderApi.getMediaFields(typeConfig.provider, activeType)
      mediaFieldsMap.value[`${typeConfig.provider}_${activeType}`] = fieldsRes.data.data || []
    }
  } catch {
    // 加载失败时使用空列表
  }
})

const MEDIA_TYPE_LABELS: Record<string, string> = { image: '图片', audio: '音频', video: '视频' }
const ALL_MEDIA_TYPES = ['image', 'audio', 'video'] as const

const imageProviders = computed(() => mediaProviderList.value.filter(p => p.supports_image))
const audioProviders = computed(() => mediaProviderList.value.filter(p => p.supports_audio))
const videoProviders = computed(() => mediaProviderList.value.filter(p => p.supports_video))

function getProvidersForType(type: string): ProviderInfo[] {
  switch (type) {
    case 'image':
      return imageProviders.value
    case 'audio':
      return audioProviders.value
    case 'video':
      return videoProviders.value
    default:
      return mediaProviderList.value
  }
}

function getProviderBaseUrl(name: string): string {
  const p = mediaProviderList.value.find(item => item.name === name)
  return p?.default_base_url || ''
}

function ensureTypeConfig(type: 'image' | 'audio' | 'video'): void {
  const existing = localConfig.value[type] as MediaGenItemConfig | undefined
  if (existing) return

  let defaultProvider = 'openai_compatible'
  const providers = getProvidersForType(type)
  if (providers.length > 0) {
    defaultProvider = providers[0].name
  }

  const params: Record<string, unknown> = {}
  const fields = mediaFieldsMap.value[`${defaultProvider}_${type}`]
  if (fields) {
    for (const f of fields) {
      params[f.name] = f.default
    }
  }

  const config: MediaGenItemConfig = {
    enabled: true,
    provider: defaultProvider,
    model: '',
    api_key: '',
    base_url: getProviderBaseUrl(defaultProvider),
    params
  }
  config.model = mediaGenProviderModels[`${defaultProvider}_${type}`] || ''

  localConfig.value[type] = config
}

async function onProviderChange(
  type: 'image' | 'audio' | 'video',
  newProvider: string
): Promise<void> {
  const typeConfig = localConfig.value[type] as MediaGenItemConfig
  if (!typeConfig) return
  typeConfig.provider = newProvider
  typeConfig.base_url = getProviderBaseUrl(newProvider)
  typeConfig.model = mediaGenProviderModels[`${newProvider}_${type}`] || ''

  let fields = mediaFieldsMap.value[`${newProvider}_${type}`]
  if (!fields) {
    try {
      const res = await aiProviderApi.getMediaFields(newProvider, type)
      fields = res.data.data || []
      mediaFieldsMap.value[`${newProvider}_${type}`] = fields
    } catch {
      fields = []
    }
  }

  const params: Record<string, unknown> = {}
  for (const f of fields) {
    params[f.name] = f.default
  }
  typeConfig.params = params
  updateConfig()
}

function updateField(type: 'image' | 'audio' | 'video', field: string, value: unknown): void {
  const typeConfig = localConfig.value[type] as MediaGenItemConfig
  if (typeConfig) {
    ;(typeConfig as Record<string, unknown>)[field] = value
  }
}

function updateParam(type: 'image' | 'audio' | 'video', name: string, value: unknown): void {
  const typeConfig = localConfig.value[type] as MediaGenItemConfig
  if (typeConfig?.params) {
    typeConfig.params[name] = value
    updateConfig()
  }
}

function getFields(type: 'image' | 'audio' | 'video'): MediaGenFieldDef[] {
  const typeConfig = localConfig.value[type] as MediaGenItemConfig | undefined
  if (!typeConfig) return []
  return mediaFieldsMap.value[`${typeConfig.provider}_${type}`] || []
}
</script>

<template>
  <div class="media-gen-config">
    <!-- 输入变量 -->
    <div class="config-section">
      <div class="section-title">
        <span>输入变量</span>
        <el-button type="primary" size="small" link @click="addInputVariable">+ 添加变量</el-button>
      </div>
      <div class="input-variables">
        <div
          v-for="(variable, index) in localConfig.input_variables"
          :key="index"
          class="input-variable"
        >
          <div class="variable-header">
            <span class="variable-index">变量 {{ index + 1 }}</span>
            <el-button type="danger" size="small" link @click="removeInputVariable(index)">
              删除
            </el-button>
          </div>
          <el-form label-width="60px" size="small">
            <el-form-item label="名称">
              <el-input v-model="variable.name" placeholder="变量名" @blur="updateConfig" />
            </el-form-item>
            <el-form-item label="类型">
              <el-select
                v-model="variable.type"
                placeholder="选择类型"
                style="width: 100%"
                @change="updateConfig"
              >
                <el-option
                  v-for="item in fieldTypeOptions"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="来源">
              <VariableSelector
                :model-value="variable.source"
                :current-node-id="currentNodeId"
                placeholder="选择变量来源"
                @update:model-value="(v: string) => updateVariableSource(index, v)"
                @update:type="t => handleSourceTypeChange(index, t)"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <div class="config-hint">
        <el-text size="small" type="info">
          添加输入变量后，在提示词/文本中通过 {{ variableFormatHint('变量名') }} 引用
        </el-text>
      </div>
    </div>

    <!-- 基础配置：执行类型 + 固定输出变量 + 动态参数 -->
    <div class="config-section">
      <div class="section-title">基础配置</div>
      <el-form label-width="90px" size="small">
        <el-form-item label="执行类型">
          <el-select
            v-model="localConfig.media_type"
            style="width: 100%"
            @change="
              (val: string) => {
                ensureTypeConfig(val as 'image' | 'audio' | 'video')
                const tc = localConfig[val] as MediaGenItemConfig
                if (tc?.provider && !mediaFieldsMap[`${tc.provider}_${val}`]) {
                  aiProviderApi.getMediaFields(tc.provider, val).then(res => {
                    mediaFieldsMap[`${tc.provider}_${val}`] = res.data.data || []
                  })
                }
                updateConfig()
              }
            "
          >
            <el-option value="image" label="图片" />
            <el-option value="audio" label="音频" />
            <el-option value="video" label="视频" />
          </el-select>
        </el-form-item>
        <template
          v-for="field in getFields(localConfig.media_type as 'image' | 'audio' | 'video')"
          :key="field.name"
        >
          <el-form-item>
            <template #label>
              <span>{{ field.label }}</span>
              <el-tooltip v-if="field.description" :content="field.description" placement="top">
                <el-icon class="field-hint-icon"><QuestionFilled /></el-icon>
              </el-tooltip>
            </template>
            <el-select
              v-if="field.field_type === 'select'"
              :model-value="
                (localConfig[localConfig.media_type] as MediaGenItemConfig).params?.[field.name] ??
                field.default
              "
              style="width: 100%"
              @change="
                (val: string) =>
                  updateParam(
                    localConfig.media_type as 'image' | 'audio' | 'video',
                    field.name,
                    val
                  )
              "
            >
              <el-option v-for="opt in field.options" :key="opt" :label="opt" :value="opt" />
            </el-select>
            <el-switch
              v-else-if="field.field_type === 'switch'"
              :model-value="
                (localConfig[localConfig.media_type] as MediaGenItemConfig).params?.[field.name] ??
                field.default
              "
              @change="
                (val: boolean) =>
                  updateParam(
                    localConfig.media_type as 'image' | 'audio' | 'video',
                    field.name,
                    val
                  )
              "
            />
            <el-slider
              v-else-if="
                field.field_type === 'number' && field.min_val != null && field.max_val != null
              "
              :model-value="
                (localConfig[localConfig.media_type] as MediaGenItemConfig).params?.[field.name] ??
                field.default
              "
              :min="field.min_val"
              :max="field.max_val"
              :step="field.step || 1"
              @change="
                (val: number) =>
                  updateParam(
                    localConfig.media_type as 'image' | 'audio' | 'video',
                    field.name,
                    val
                  )
              "
            />
            <el-input
              v-else-if="field.field_type === 'textarea'"
              type="textarea"
              :rows="3"
              :model-value="
                (localConfig[localConfig.media_type] as MediaGenItemConfig).params?.[field.name] ??
                field.default
              "
              :placeholder="field.placeholder"
              @input="
                (val: string) =>
                  updateParam(
                    localConfig.media_type as 'image' | 'audio' | 'video',
                    field.name,
                    val
                  )
              "
            />
            <el-input
              v-else-if="field.field_type === 'number'"
              type="number"
              :model-value="
                (localConfig[localConfig.media_type] as MediaGenItemConfig).params?.[field.name] ??
                field.default
              "
              :placeholder="field.placeholder"
              @input="
                (val: string) =>
                  updateParam(
                    localConfig.media_type as 'image' | 'audio' | 'video',
                    field.name,
                    val
                  )
              "
            />
            <el-input
              v-else
              :model-value="
                (localConfig[localConfig.media_type] as MediaGenItemConfig).params?.[field.name] ??
                field.default
              "
              :placeholder="field.placeholder"
              @input="
                (val: string) =>
                  updateParam(
                    localConfig.media_type as 'image' | 'audio' | 'video',
                    field.name,
                    val
                  )
              "
            />
          </el-form-item>
        </template>
      </el-form>
    </div>

    <!-- 三种模态配置 -->
    <div v-for="type in ALL_MEDIA_TYPES" :key="type" class="config-section">
      <div class="section-title">{{ MEDIA_TYPE_LABELS[type] }}配置</div>
      <el-form label-width="90px" size="small">
        <el-form-item label="供应商">
          <el-select
            :model-value="(localConfig[type] as MediaGenItemConfig)?.provider ?? ''"
            style="width: 100%"
            @change="
              (val: string) => {
                ensureTypeConfig(type)
                onProviderChange(type, val)
              }
            "
          >
            <el-option
              v-for="item in getProvidersForType(type)"
              :key="item.name"
              :label="item.label"
              :value="item.name"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="模型">
          <el-input
            :model-value="(localConfig[type] as MediaGenItemConfig)?.model ?? ''"
            :placeholder="`${MEDIA_TYPE_LABELS[type]}生成模型`"
            @input="
              (val: string) => {
                ensureTypeConfig(type)
                updateField(type, 'model', val)
              }
            "
            @blur="updateConfig"
          />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            :model-value="(localConfig[type] as MediaGenItemConfig)?.api_key ?? ''"
            type="password"
            placeholder="输入 API Key"
            show-password
            @input="
              (val: string) => {
                ensureTypeConfig(type)
                updateField(type, 'api_key', val)
              }
            "
            @blur="updateConfig"
          />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input
            :model-value="(localConfig[type] as MediaGenItemConfig)?.base_url ?? ''"
            placeholder="API 地址"
            @input="
              (val: string) => {
                ensureTypeConfig(type)
                updateField(type, 'base_url', val)
              }
            "
            @blur="updateConfig"
          />
        </el-form-item>
        <el-form-item label="启用工具">
          <el-switch
            :model-value="(localConfig[type] as MediaGenItemConfig)?.enabled ?? false"
            @change="
              (val: boolean) => {
                ensureTypeConfig(type)
                updateField(type, 'enabled', val)
                updateConfig()
              }
            "
          />
        </el-form-item>
      </el-form>
    </div>

    <!-- 输出变量 -->
    <div class="config-section">
      <div class="section-title">输出变量</div>
      <div class="output-variables-info">
        <div v-for="ov in localConfig.output_variables" :key="ov.name" class="output-var-tag">
          <el-tag size="small" type="info">{{ ov.name }}</el-tag>
          <span class="output-var-type">{{ ov.type || '' }}</span>
        </div>
        <el-text size="small" type="info">下游节点通过变量映射使用</el-text>
      </div>
    </div>

    <!-- 提示信息 -->
    <div class="config-section">
      <div class="config-hint">
        <el-text size="small" type="info">
          独立执行：流程执行到该节点时，按「执行类型」指定的配置生成，结果存入输出变量。
          <br />
          工具提供：三种类型均自动作为工具暴露给 LLM。
          <br />
          提示词/文本类参数支持变量引用：{{ variableFormatHint('input.xxx') }} 输入数据 |
          {{ variableFormatHint('variables.xxx') }} 流程变量 |
          {{ variableFormatHint('nodes.节点.变量') }} 节点输出
        </el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';

.field-hint-icon {
  margin-left: 4px;
  color: var(--el-text-color-placeholder);
  cursor: pointer;
  font-size: 14px;
  vertical-align: middle;
}

.output-variables-info {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.output-var-tag {
  display: flex;
  align-items: center;
  gap: 4px;
}
.output-var-type {
  font-size: 12px;
  color: #909399;
}
</style>
