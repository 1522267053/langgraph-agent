<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { LlmConfig } from './types'
import { llmModels, variableFormatHint, CONTEXT_LENGTH_PRESETS } from './types'
import { fieldTypeOptions, parseContextLength } from './types'
import { aiProviderApi, type ProviderInfo } from '@/api/ai_provider'
import { flowApi, type ConnectedToolInfo } from '@/api/flow'
import { useFlowStore } from '@/stores/flowStore'
import { useConfigBase } from '@/composables/useConfigBase'
import { useInputVariables } from '@/composables/useInputVariables'
import VariableSelector from '../components/VariableSelector.vue'

const props = defineProps<{
  config: LlmConfig
  currentNodeId: string
  isAgentMode?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:config', value: LlmConfig): void
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

if (!localConfig.value.capabilities) {
  localConfig.value.capabilities = {
    image: false,
    video: false,
    audio: false,
    pdf: false,
    xlsx: false
  }
}

// 必需工具检查默认值（兼容旧配置）
if (!localConfig.value.required_tools) {
  localConfig.value.required_tools = []
}
if (localConfig.value.tool_check_script === undefined) {
  localConfig.value.tool_check_script = ''
}
if (!localConfig.value.required_tools_max_retries) {
  localConfig.value.required_tools_max_retries = 2
}
if (localConfig.value.required_tools_hint === undefined) {
  localConfig.value.required_tools_hint = ''
}

watch(
  () => props.config,
  () => {
    if (!localConfig.value.capabilities) {
      localConfig.value.capabilities = {
        image: false,
        video: false,
        audio: false,
        pdf: false,
        xlsx: false
      }
    }
    // 必需工具检查默认值（每次配置变化都补齐，防止被 useConfigBase 覆盖）
    if (!localConfig.value.required_tools) {
      localConfig.value.required_tools = []
    }
    if (localConfig.value.tool_check_script === undefined) {
      localConfig.value.tool_check_script = ''
    }
    if (!localConfig.value.required_tools_max_retries) {
      localConfig.value.required_tools_max_retries = 2
    }
    if (localConfig.value.required_tools_hint === undefined) {
      localConfig.value.required_tools_hint = ''
    }
  }
)
const { addInputVariable, removeInputVariable, handleSourceTypeChange } = useInputVariables(
  localConfig,
  updateConfig
)

// ---- 动态供应商列表 ----

const providerList = ref<ProviderInfo[]>([])

onMounted(async () => {
  try {
    const res = await aiProviderApi.list()
    providerList.value = res.data.data || []
  } catch {
    // 加载失败时使用空列表
  }
})

function getProviderBaseUrl(name: string): string {
  const p = providerList.value.find(item => item.name === name)
  return p?.default_base_url || ''
}

const currentModels = computed(() => {
  return llmModels[localConfig.value.provider] || []
})

const selectedModelOption = computed(() => {
  return currentModels.value.find(m => m.value === localConfig.value.model)
})

// ---- 必需工具检查 ----
// 使用独立 ref 控制，避免从配置反推导致开关/模式切换时状态弹回
const enableRequiredTools = ref(
  (localConfig.value.required_tools?.length ?? 0) > 0 || !!localConfig.value.tool_check_script
)

watch(enableRequiredTools, val => {
  if (!val) {
    localConfig.value.required_tools = []
    localConfig.value.tool_check_script = ''
    updateConfig()
  }
})

const requiredToolsMode = ref(localConfig.value.tool_check_script ? 'script' : 'simple')

watch(requiredToolsMode, val => {
  if (val === 'simple') {
    localConfig.value.tool_check_script = ''
  } else {
    localConfig.value.required_tools = []
  }
  updateConfig()
})

// ---- 已连接工具下拉 ----

const flowStore = useFlowStore()

const connectedToolGroups = ref<ConnectedToolInfo[]>([])

async function fetchConnectedTools(): Promise<void> {
  const flowId = flowStore.flowInfo?.id
  if (!flowId || !props.currentNodeId) {
    connectedToolGroups.value = []
    return
  }
  try {
    const res = await flowApi.getConnectedTools(flowId, props.currentNodeId)
    if (res.data.code === 1 && res.data.data) {
      connectedToolGroups.value = res.data.data
    } else {
      connectedToolGroups.value = []
    }
  } catch {
    connectedToolGroups.value = []
  }
}

watch(
  () => props.currentNodeId,
  () => {
    fetchConnectedTools()
  },
  { immediate: true }
)

watch(
  () => localConfig.value.provider,
  () => {
    if (!isLoadingConfig.value) {
      localConfig.value.model = ''
      localConfig.value.capabilities = {
        image: false,
        video: false,
        audio: false,
        pdf: false,
        xlsx: false
      }
      localConfig.value.context_length = undefined
      const defaultBaseUrl = getProviderBaseUrl(localConfig.value.provider)
      if (defaultBaseUrl) {
        localConfig.value.base_url = defaultBaseUrl
      }
    }
  },
  { flush: 'sync' }
)

watch(
  () => localConfig.value.model,
  () => {
    if (!isLoadingConfig.value) {
      if (selectedModelOption.value?.capabilities) {
        localConfig.value.capabilities = { ...selectedModelOption.value.capabilities }
      }
      if (selectedModelOption.value?.context_length) {
        localConfig.value.context_length = selectedModelOption.value.context_length
      } else if (!isLoadingConfig.value) {
        localConfig.value.context_length = undefined
      }
    }
  },
  { flush: 'sync' }
)

function handleCapabilityChange(): void {
  updateConfig()
}

function onContextLengthChange(val: string | number | undefined): void {
  if (val !== undefined && val !== null && val !== '') {
    const parsed = parseContextLength(val)
    if (parsed === undefined) {
      ElMessage.error('上下文窗口格式无效，请输入数字或带单位（如 32000、32K、1M）')
      nextTick(() => {
        localConfig.value.context_length = undefined
        updateConfig()
      })
      return
    }
    localConfig.value.context_length = parsed as any
  } else {
    localConfig.value.context_length = undefined
  }
  updateConfig()
}

const extraBodyText = ref('')

watch(
  () => localConfig.value.extra_body,
  eb => {
    extraBodyText.value = eb && Object.keys(eb).length > 0 ? JSON.stringify(eb, null, 2) : ''
  },
  { immediate: true }
)

function handleExtraBodyBlur(): void {
  const text = extraBodyText.value.trim()
  if (!text) {
    localConfig.value.extra_body = {}
  } else {
    try {
      localConfig.value.extra_body = JSON.parse(text)
    } catch {
      return
    }
  }
  updateConfig()
}
</script>

<template>
  <div class="llm-config">
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
                @change="onContextLengthChange"
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
                v-model="variable.source"
                :current-node-id="currentNodeId"
                placeholder="选择变量来源"
                @update:model-value="updateConfig"
                @update:type="t => handleSourceTypeChange(index, t)"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <div class="config-hint">
        <el-text size="small" type="info">使用下拉选择器选择变量来源</el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">大模型配置</div>
      <el-form label-width="90px" size="small">
        <el-form-item label="供应商">
          <el-select
            v-model="localConfig.provider"
            placeholder="选择供应商"
            style="width: 100%"
            @change="updateConfig"
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
            v-model="localConfig.model"
            placeholder="选择或输入模型名称"
            style="width: 100%"
            :disabled="!localConfig.provider"
            filterable
            allow-create
            default-first-option
            @change="updateConfig"
          >
            <el-option
              v-for="item in currentModels"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="支持内容">
          <el-checkbox v-model="localConfig.capabilities.image" @change="handleCapabilityChange">
            图片
          </el-checkbox>
          <el-checkbox v-model="localConfig.capabilities.video" @change="handleCapabilityChange">
            视频
          </el-checkbox>
          <el-checkbox v-model="localConfig.capabilities.audio" @change="handleCapabilityChange">
            音频
          </el-checkbox>
          <el-checkbox v-model="localConfig.capabilities.pdf" @change="handleCapabilityChange">
            PDF
          </el-checkbox>
          <el-checkbox v-model="localConfig.capabilities.xlsx" @change="handleCapabilityChange">
            Excel
          </el-checkbox>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="localConfig.api_key"
            type="password"
            placeholder="留空使用全局默认 API Key"
            show-password
            @blur="updateConfig"
          />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input
            v-model="localConfig.base_url"
            placeholder="可选，自定义接口地址"
            @blur="updateConfig"
          />
        </el-form-item>
        <el-form-item>
          <template #label>
            上下文窗口
            <el-tooltip
              content="用于上下文自动压缩，当对话占用超过 80% 时自动压缩旧消息。不填则不会自动压缩。"
            >
              <el-icon class="context-tip-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <el-select
            v-model="localConfig.context_length"
            placeholder="选择或输入上下文大小"
            style="width: calc(100% - 80px)"
            filterable
            allow-create
            default-first-option
            clearable
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
      </el-form>
    </div>
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
    <div class="config-section">
      <div class="section-title">提示词配置</div>
      <el-form label-width="90px" size="small">
        <el-form-item label="系统提示词">
          <el-input
            v-model="localConfig.system_prompt"
            type="textarea"
            :rows="3"
            placeholder="定义 LLM 的角色和行为，如：你是一个翻译助手。支持变量插值"
            @blur="updateConfig"
          />
        </el-form-item>
        <el-form-item label="用户消息模板">
          <el-input
            v-model="localConfig.user_prompt"
            type="textarea"
            :rows="4"
            placeholder="必填，LLM 接收的用户消息。支持变量插值，如：{{message}}"
            @blur="updateConfig"
          />
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          变量选择器：{{ variableFormatHint('input.xxx') }} 输入数据 |
          {{ variableFormatHint('nodes.节点.变量') }} 节点输出
          <br />
          模板引用：{{ variableFormatHint('&#123;&#123;变量名&#125;&#125;') }} 节点内变量 |
          {{ variableFormatHint('&#123;&#123;input.xxx&#125;&#125;') }} 输入数据 |
          {{ variableFormatHint('&#123;&#123;variables.xxx&#125;&#125;') }} 流程变量
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">高级配置</div>
      <el-form label-width="120px" size="small">
        <el-form-item label="温度(temperature)">
          <el-input-number
            v-model="localConfig.temperature"
            :min="0"
            :max="2"
            :step="0.1"
            :precision="1"
            controls-position="right"
            @change="updateConfig"
          />
        </el-form-item>
        <el-form-item label="工具调用最大次数">
          <el-input-number
            v-model="localConfig.max_tool_iterations"
            :min="1"
            :max="100"
            @change="updateConfig"
          />
        </el-form-item>
        <el-form-item label="最大输出Token">
          <el-input-number
            v-model="localConfig.max_tokens"
            :min="256"
            :max="128000"
            :step="1024"
            @change="updateConfig"
          />
        </el-form-item>
        <template v-if="!isAgentMode">
          <el-form-item label="对话历史模式">
            <el-radio-group v-model="localConfig.history_mode" @change="updateConfig">
              <el-radio value="node">节点内</el-radio>
              <el-radio value="flow">全流程</el-radio>
              <el-radio value="none">无历史</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="localConfig.history_mode !== 'none'" label="历史记录最大轮数">
            <el-input-number
              v-model="localConfig.max_history_turns"
              :min="1"
              :max="100"
              @change="updateConfig"
            />
          </el-form-item>
        </template>
        <template v-if="isAgentMode">
          <el-form-item label="工具确认">
            <el-switch v-model="localConfig.require_tool_approval" @change="updateConfig" />
            <el-text size="small" type="info" style="margin-left: 8px">
              Shell/Python工具执行前需用户确认
            </el-text>
          </el-form-item>
        </template>
        <el-form-item label="推理深度">
          <el-select
            v-model="localConfig.reasoning_effort"
            placeholder="不设置（使用模型默认）"
            style="width: 100%"
            clearable
            filterable
            allow-create
            default-first-option
            @change="updateConfig"
          >
            <el-option label="low" value="low" />
            <el-option label="medium" value="medium" />
            <el-option label="high" value="high" />
          </el-select>
        </el-form-item>
        <el-form-item label="附加参数">
          <el-input
            v-model="extraBodyText"
            type="textarea"
            :rows="2"
            placeholder='JSON 格式，如: {"enable_search": true}'
            @blur="handleExtraBodyBlur"
          />
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          工具调用：通过拖动MCP节点连接到此LLM节点
          <br />
          人工协助：通过拖动Human节点连接到此LLM节点
          <br />
          其他带有工具的节点：通过拖动其他节点连接到此LLM节点
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">必需工具检查</div>
      <el-form label-width="120px" size="small">
        <el-form-item label="启用检查">
          <el-switch v-model="enableRequiredTools" />
          <el-text size="small" type="info" style="margin-left: 8px">
            LLM 未调用必需工具时自动提醒重试
          </el-text>
        </el-form-item>
        <template v-if="enableRequiredTools">
          <el-form-item label="检查模式">
            <el-radio-group v-model="requiredToolsMode">
              <el-radio value="simple">工具名匹配</el-radio>
              <el-radio value="script">自定义脚本</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="requiredToolsMode === 'simple'" label="必需工具名">
            <el-select
              v-model="localConfig.required_tools"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="输入或选择工具名"
              style="width: 100%"
              @change="updateConfig"
            >
              <el-option-group
                v-for="group in connectedToolGroups"
                :key="group.node_key"
                :label="group.node_label"
              >
                <el-option
                  v-for="tool in group.tools"
                  :key="tool.name"
                  :label="tool.name"
                  :value="tool.name"
                />
              </el-option-group>
            </el-select>
          </el-form-item>
          <el-form-item v-else label="检查脚本">
            <el-input
              v-model="localConfig.tool_check_script"
              type="textarea"
              :rows="5"
              placeholder="def main(called_tools, last_result): return {'need_retry': bool, 'hint': str}"
              @blur="updateConfig"
            />
          </el-form-item>
          <el-form-item label="最大重试次数">
            <el-input-number
              v-model="localConfig.required_tools_max_retries"
              :min="1"
              :max="10"
              @change="updateConfig"
            />
          </el-form-item>
          <el-form-item v-if="requiredToolsMode === 'simple'" label="提醒模板">
            <el-input
              v-model="localConfig.required_tools_hint"
              type="textarea"
              :rows="2"
              placeholder="留空使用默认模板，{{tools}} 会被替换为缺失的工具名"
              @blur="updateConfig"
            />
          </el-form-item>
        </template>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          仅检查本次对话新调用的工具（不查历史）。Agent 模式下重试在 LLM
          内部完成，用户只看到最终回复
        </el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';

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
