<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import type { BrowserConfig } from './types'
import { fieldTypeOptions } from './types'
import { useConfigBase } from '@/composables/useConfigBase'
import { useInputVariables } from '@/composables/useInputVariables'
import { flowApi, type BrowserActionMeta, type BrowserActionParam } from '@/api/flow'
import VariableSelector from '../components/VariableSelector.vue'
import ApprovalConfigSection, { type ApprovalConfig } from './ApprovalConfigSection.vue'

const props = defineProps<{
  config: BrowserConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: BrowserConfig): void
}>()

const { localConfig, updateConfig } = useConfigBase(() => props.config, emit)
const { addInputVariable, removeInputVariable, handleSourceTypeChange } = useInputVariables(
  localConfig,
  updateConfig
)

function updateVariableSource(index: number, source: string): void {
  if (localConfig.value.input_variables[index])
    localConfig.value.input_variables[index].source = source
  updateConfig()
}

// ---- 工具审批配置（复用 ApprovalConfigSection，同 shell/ssh 下沉模式）----

function ensureApprovalArrays(): void {
  if (!Array.isArray(localConfig.value.approval_required_tools)) {
    localConfig.value.approval_required_tools = []
  }
  if (!Array.isArray(localConfig.value.approval_required_patterns)) {
    localConfig.value.approval_required_patterns = []
  }
}
ensureApprovalArrays()

const browserAvailableTools = [
  { label: 'browser_start', value: 'browser_start' },
  { label: 'browser_snapshot', value: 'browser_snapshot' },
  { label: 'browser_navigate', value: 'browser_navigate' },
  { label: 'browser_click', value: 'browser_click' },
  { label: 'browser_input', value: 'browser_input' },
  { label: 'browser_screenshot', value: 'browser_screenshot' },
  { label: 'browser_get_content', value: 'browser_get_content' },
  { label: 'browser_evaluate', value: 'browser_evaluate' },
  { label: 'browser_tabs', value: 'browser_tabs' },
  { label: 'browser_scroll', value: 'browser_scroll' },
  { label: 'browser_wait_for', value: 'browser_wait_for' },
  { label: 'browser_close', value: 'browser_close' }
]

function onApprovalUpdate(val: ApprovalConfig): void {
  localConfig.value.approval_required_tools = [...val.approval_required_tools]
  localConfig.value.approval_required_patterns = [...val.approval_required_patterns]
  updateConfig()
}

// ---- Flow 模式：选动作带出参数表单（后端单源，MCP 同款动态拉取）----

const browserActions = ref<BrowserActionMeta[]>([])

/** 拉取失败时置空数组：参数表单不渲染、下拉退化仅显示 action 值，不会白屏 */
async function fetchBrowserActions(): Promise<void> {
  try {
    const res = await flowApi.getBrowserActions()
    if (res.data.code === 1 && Array.isArray(res.data.data)) {
      browserActions.value = res.data.data
    } else {
      browserActions.value = []
    }
  } catch {
    browserActions.value = []
  }
}

onMounted(fetchBrowserActions)

const currentActionMeta = computed<BrowserActionMeta | undefined>(() =>
  browserActions.value.find(a => a.action === localConfig.value.flow_action)
)

const selectedActionParams = computed<BrowserActionParam[]>(
  () => currentActionMeta.value?.params || []
)

/** 切换动作时重置参数绑定（保留同名参数旧值，MCP onToolNameChange 同款） */
function onActionChange(): void {
  const oldArgs = { ...(localConfig.value.flow_args || {}) }
  const next: Record<string, string> = {}
  for (const p of selectedActionParams.value) {
    if (oldArgs[p.name] !== undefined) next[p.name] = oldArgs[p.name]
  }
  localConfig.value.flow_args = next
  updateConfig()
}
</script>

<template>
  <div class="browser-config">
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
        <el-text size="small" type="info">浏览器会话按流程自动管理，通常无需配置输入变量</el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">连接模式</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="模式">
          <el-radio-group v-model="localConfig.connect_mode" @change="updateConfig">
            <el-radio value="launch">独立实例（推荐）</el-radio>
            <el-radio value="remote">远程浏览器</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          独立实例：每流程自动启动自己的浏览器（自动分配端口，多流程并发互不干扰），无需任何手动配置
        </el-text>
      </div>
    </div>

    <template v-if="localConfig.connect_mode === 'launch'">
      <div class="config-section">
        <div class="section-title">独立实例设置</div>
        <el-form label-width="80px" size="small">
          <el-form-item label="浏览器路径">
            <el-input
              v-model="localConfig.browser_path"
              placeholder="留空自动探测 Chrome/Edge；或填 chrome.exe 绝对路径，支持 {{path}} 变量"
              @blur="updateConfig"
            />
          </el-form-item>
          <el-form-item label="数据目录">
            <el-input
              v-model="localConfig.user_data_path"
              placeholder="留空=临时目录（退出清理）；填写则登录态按流程持久化，支持 {{变量}} 插值"
              @blur="updateConfig"
            />
          </el-form-item>
          <el-form-item label="调试端口">
            <el-input-number
              v-model="localConfig.debug_port"
              :min="0"
              :max="65535"
              @change="updateConfig"
            />
            <span class="unit-label">0=自动探测；固定端口便于外部 DevTools 接入</span>
          </el-form-item>
          <el-form-item label="操作超时">
            <el-input-number
              v-model="localConfig.action_timeout"
              :min="5"
              :max="300"
              @change="updateConfig"
            />
            <span class="unit-label">秒</span>
          </el-form-item>
          <el-form-item label="无头模式">
            <el-switch v-model="localConfig.headless" @change="updateConfig" />
            <span class="unit-label">开启=后台运行不弹窗；关闭=弹出真实浏览器窗口（调试用）</span>
          </el-form-item>
        </el-form>
        <div class="config-hint">
          <el-text size="small" type="info">
            数据目录填写后按流程派生子目录（flow_id 隔离），登录态/Cookie 跨运行保留
          </el-text>
        </div>
      </div>

      <div class="config-section">
        <div class="section-title">Flow 模式动作</div>
        <el-form label-width="80px" size="small">
          <el-form-item label="动作">
            <el-select
              v-model="localConfig.flow_action"
              style="width: 100%"
              @change="onActionChange"
            >
              <el-option v-for="a in browserActions" :key="a.action" :label="a.label" :value="a.action" />
            </el-select>
          </el-form-item>
          <el-form-item
            v-for="param in selectedActionParams"
            :key="param.name"
            :required="param.required"
          >
            <template #label>
              {{ param.name }}
              <el-tooltip
                v-if="param.description"
                :content="`[${param.type}] ${param.description}`"
                placement="top"
              >
                <el-icon class="param-tip-icon" @click.prevent.stop><QuestionFilled /></el-icon>
              </el-tooltip>
            </template>
            <el-input
              v-model="localConfig.flow_args[param.name]"
              :placeholder="param.type"
              @change="updateConfig"
            />
          </el-form-item>
          <el-form-item v-if="selectedActionParams.length" label="">
            <el-text size="small" type="info">
              参数值支持变量插值：双大括号包裹变量路径（语法同 API 节点，如输入变量的 message）
            </el-text>
          </el-form-item>
        </el-form>
        <div class="config-hint">
          <el-text size="small" type="info">
            每个浏览器节点执行一个动作；同流程串联多个浏览器节点时自动复用同一个浏览器实例 （start →
            navigate → input → click → get_content → … → close）
          </el-text>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="config-section">
        <div class="section-title">远程浏览器（高级）</div>
        <el-form label-width="80px" size="small">
          <el-form-item label="主机">
            <el-input
              v-model="localConfig.remote_host"
              placeholder="远程主机 IP（如 192.168.1.100）"
              @blur="updateConfig"
            />
          </el-form-item>
          <el-form-item label="调试端口">
            <el-input-number
              v-model="localConfig.remote_port"
              :min="1"
              :max="65535"
              @change="updateConfig"
            />
          </el-form-item>
        </el-form>
        <div class="config-hint">
          <el-text size="small" type="warning">
            远程主机需自行以 --remote-debugging-port={{ localConfig.remote_port }} 启动浏览器； CDP
            协议无认证，仅限可信内网使用。多个流程连接同一远程浏览器会互相干扰
          </el-text>
        </div>
      </div>
    </template>

    <div class="config-section">
      <div class="section-title">工具能力</div>
      <div class="config-hint">
        <el-text size="small" type="info">
          连接到 LLM 后，AI 可自主调用 12
          个工具：browser_start（开启浏览器）、browser_snapshot（页面快照，枢纽）、navigate / click
          / input / screenshot / get_content / evaluate / tabs / scroll / wait_for /
          close；典型流程为「start 开浏览器 → navigate 访问页面 → snapshot 查元素 → click/input 操作
          → snapshot 确认 → close 关闭」
        </el-text>
      </div>
    </div>

    <ApprovalConfigSection
      :model-value="{
        approval_required_tools: localConfig.approval_required_tools,
        approval_required_patterns: localConfig.approval_required_patterns
      }"
      :available-tools="browserAvailableTools"
      hint="浏览器可操作用户登录态的真实网站，敏感操作（如支付、发帖、删除）建议将对应工具加入审批白名单；仅命中的工具弹审批，其余默认放行。"
      @update:model-value="onApprovalUpdate"
    />

    <div class="config-section">
      <div class="section-title">输出变量</div>
      <div class="output-variables-info">
        <div v-for="ov in localConfig.output_variables" :key="ov.name" class="output-var-tag">
          <el-tag size="small" type="info">{{ ov.name }}</el-tag>
          <span class="output-var-type">{{ ov.type || '' }}</span>
        </div>
        <el-text size="small" type="info">
          动作执行结果写入（object，下游用 nodes.节点key.result.url / .content / .note
          等子路径取字段；screenshot 为图片预览链接）
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
</style>
