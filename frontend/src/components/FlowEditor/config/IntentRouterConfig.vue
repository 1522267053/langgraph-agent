<script setup lang="ts">
import { computed, ref } from 'vue'
import { Plus, Delete } from '@element-plus/icons-vue'
import type { IntentItem, IntentRouterConfig } from './types'
import { useConfigBase } from '@/composables/useConfigBase'
import VariableSelector from '../components/VariableSelector.vue'
import AiProviderConfig from '@/components/common/AiProviderConfig.vue'

const props = defineProps<{
  config: IntentRouterConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: IntentRouterConfig): void
}>()

const { localConfig, updateConfig } = useConfigBase(
  () => ({
    ...props.config,
    intents: props.config?.intents ?? [],
    input_variable: props.config?.input_variable ?? ''
  }),
  emit
)

// ---- 校验：意图 key 必须是 slug，不能重复，不能是保留字 ----
const RESERVED_KEYS = new Set(['default', 'tools', 'true', 'false'])
const KEY_PATTERN = /^[a-zA-Z_][a-zA-Z0-9_]*$/
const regexErrorMap = ref<Record<string, string>>({})

function validateKey(key: string, idx: number, list: IntentItem[]): string {
  if (!key) return 'key 不能为空'
  if (!KEY_PATTERN.test(key)) return '只能包含字母、数字、下划线，不能以数字开头'
  if (RESERVED_KEYS.has(key)) return `保留字：${key}`
  if (list.findIndex((it, i) => i !== idx && it.key === key) >= 0) return 'key 重复'
  return ''
}

const keyErrors = computed<Record<number, string>>(() => {
  const errs: Record<number, string> = {}
  localConfig.value.intents.forEach((it, idx) => {
    const msg = validateKey(it.key, idx, localConfig.value.intents)
    if (msg) errs[idx] = msg
  })
  return errs
})

// ---- 校验：正则编译（前端双保险，与后端一致） ----
function validateRegex(pattern: string): string {
  if (!pattern) return ''
  try {
    new RegExp(pattern)
    return ''
  } catch (e) {
    return (e as Error).message
  }
}

function onRegexInput(intentIdx: number, pIdx: number, value: string) {
  const err = validateRegex(value)
  if (err) regexErrorMap.value[`${intentIdx}-${pIdx}`] = err
  else delete regexErrorMap.value[`${intentIdx}-${pIdx}`]
  updateConfig()
}

// ---- 意图列表操作 ----
function addIntent() {
  localConfig.value.intents.push({
    key: '',
    description: '',
    examples: [],
    rule: { keywords: [], regex_patterns: [] }
  })
  updateConfig()
}

function removeIntent(idx: number) {
  localConfig.value.intents.splice(idx, 1)
  updateConfig()
}

// ---- 输出变量（只读展示） ----
const outputVariables = [
  { name: 'intent', desc: '命中的意图 key' },
  { name: 'raw_response', desc: 'LLM 原始输出' },
  { name: 'metadata', desc: '完整执行详情' }
]

// 下游引用示例（避免 Vue 模板内双花括号解析错误）
const downstreamRefExample = '{{nodes.节点key.intent}}'
</script>

<template>
  <div class="intent-router-config">
    <!-- 1. 输入 -->
    <div class="config-section">
      <div class="section-title">输入</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="待分类文本">
          <VariableSelector
            v-model="localConfig.input_variable"
            :current-node-id="currentNodeId"
            placeholder="选择变量（默认 input.question）"
            @update:model-value="updateConfig"
          />
        </el-form-item>
      </el-form>
    </div>

    <!-- 2. 层级设置 -->
    <div class="config-section">
      <div class="section-title">层级设置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="规则层">
          <el-switch v-model="localConfig.enable_rule_layer" @change="updateConfig" />
          <el-text size="small" type="info" class="hint">关键字 + 正则</el-text>
        </el-form-item>
        <el-form-item label="LLM 层">
          <el-switch v-model="localConfig.enable_llm_layer" @change="updateConfig" />
          <el-text size="small" type="info" class="hint">规则层未命中才调用</el-text>
        </el-form-item>
        <el-form-item label="大小写敏感">
          <el-switch v-model="localConfig.case_sensitive" @change="updateConfig" />
          <el-text size="small" type="info" class="hint">仅影响规则层</el-text>
        </el-form-item>
      </el-form>
    </div>

    <!-- 3. 意图列表（核心，放显眼位置） -->
    <div class="config-section">
      <div class="section-title">
        <span>意图列表 ({{ localConfig.intents.length }})</span>
        <el-button type="primary" size="small" link :icon="Plus" @click="addIntent">
          添加意图
        </el-button>
      </div>

      <div v-if="localConfig.intents.length === 0" class="empty-text">
        暂无意图，点击「添加意图」开始
      </div>

      <div v-else class="intent-cards">
        <div v-for="(intent, idx) in localConfig.intents" :key="idx" class="intent-card">
          <div class="intent-card-header">
            <div class="intent-card-title">
              <span class="intent-key-badge" :class="{ invalid: keyErrors[idx] }">
                {{ intent.key || '(未命名)' }}
              </span>
              <span v-if="intent.description" class="intent-desc-preview">
                {{ intent.description }}
              </span>
              <span v-if="keyErrors[idx]" class="intent-error">{{ keyErrors[idx] }}</span>
            </div>
            <el-button type="danger" size="small" link :icon="Delete" @click="removeIntent(idx)" />
          </div>

          <el-form label-width="70px" size="small">
            <el-form-item label="Key" required>
              <el-input
                v-model="intent.key"
                placeholder="英文 slug，如 billing"
                :class="{ 'is-error': keyErrors[idx] }"
                @blur="updateConfig"
              />
            </el-form-item>
            <el-form-item label="描述">
              <el-input
                v-model="intent.description"
                type="textarea"
                :rows="2"
                placeholder="给 LLM 看的意图说明"
                @blur="updateConfig"
              />
            </el-form-item>
            <el-form-item label="示例">
              <el-select
                v-model="intent.examples"
                multiple
                filterable
                allow-create
                default-first-option
                placeholder="输入示例后回车（可多个）"
                style="width: 100%"
                @change="updateConfig"
              >
                <el-option v-for="ex in intent.examples" :key="ex" :label="ex" :value="ex" />
              </el-select>
            </el-form-item>

            <template v-if="localConfig.enable_rule_layer">
              <el-divider content-position="left">规则（任一命中即归类）</el-divider>
              <el-form-item label="关键字">
                <el-select
                  v-model="intent.rule.keywords"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  placeholder="包含匹配，输入后回车"
                  style="width: 100%"
                  @change="updateConfig"
                >
                  <el-option v-for="kw in intent.rule.keywords" :key="kw" :label="kw" :value="kw" />
                </el-select>
              </el-form-item>
              <el-form-item label="正则">
                <div class="regex-list">
                  <div
                    v-for="(p, pIdx) in intent.rule.regex_patterns"
                    :key="pIdx"
                    class="regex-row"
                  >
                    <el-input
                      :model-value="p"
                      placeholder="如 ^订单\d+$"
                      :class="{ 'is-error': regexErrorMap[`${idx}-${pIdx}`] }"
                      @update:model-value="
                        (v: string) => {
                          intent.rule.regex_patterns[pIdx] = v
                          onRegexInput(idx, pIdx, v)
                        }
                      "
                    />
                    <el-button
                      type="danger"
                      size="small"
                      link
                      :icon="Delete"
                      @click="
                        () => {
                          intent.rule.regex_patterns.splice(pIdx, 1)
                          delete regexErrorMap[`${idx}-${pIdx}`]
                          updateConfig()
                        }
                      "
                    />
                  </div>
                  <el-button
                    size="small"
                    link
                    type="primary"
                    @click="
                      () => {
                        intent.rule.regex_patterns.push('')
                        updateConfig()
                      }
                    "
                  >
                    + 添加正则
                  </el-button>
                </div>
              </el-form-item>
            </template>
          </el-form>
        </div>
      </div>
    </div>

    <div v-if="localConfig.enable_llm_layer" class="config-section">
      <div class="section-title">LLM 配置</div>
      <AiProviderConfig
        v-model:provider="localConfig.provider"
        v-model:model="localConfig.model"
        v-model:api-key="localConfig.api_key"
        v-model:base-url="localConfig.base_url"
        v-model:temperature="localConfig.temperature"
        v-model:max-tokens="localConfig.max_tokens"
        v-model:reasoning-effort="localConfig.reasoning_effort"
        v-model:extra-body="localConfig.extra_body"
        show-temperature
        show-max-tokens
        show-reasoning-effort
        show-extra-body
        provider-clearable
        model-clearable
        api-key-placeholder="留空使用全局默认"
        @change="updateConfig"
      />
    </div>

    <!-- 5. 高级配置 -->
    <div class="config-section">
      <div class="section-title">高级配置</div>
      <el-form label-width="120px" size="small">
        <el-form-item label="置信度阈值">
          <el-input-number
            v-model="localConfig.confidence_threshold"
            :min="0"
            :max="1"
            :step="0.05"
            :precision="2"
            controls-position="right"
            @change="updateConfig"
          />
          <el-text size="small" type="info" class="hint">低于阈值走 default</el-text>
        </el-form-item>
        <el-form-item v-if="localConfig.enable_llm_layer" label="System Prompt 追加">
          <el-input
            v-model="localConfig.system_prompt"
            type="textarea"
            :rows="3"
            placeholder="可选：追加到默认 prompt 末尾（如分类口径说明）"
            @blur="updateConfig"
          />
        </el-form-item>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          规则层：按列表顺序匹配，第一个命中即归类（短路）；LLM 层：规则层未命中才调用，输出 JSON
          含置信度。
        </el-text>
      </div>
    </div>

    <!-- 6. 输出变量（只读） -->
    <div class="config-section">
      <div class="section-title">输出变量</div>
      <div class="output-variables-info">
        <div v-for="ov in outputVariables" :key="ov.name" class="output-var-tag">
          <el-tag size="small" type="info">{{ ov.name }}</el-tag>
          <span class="output-var-desc">{{ ov.desc }}</span>
        </div>
      </div>
      <div class="config-hint">
        <el-text size="small" type="info">
          下游节点通过
          <code>{{ downstreamRefExample }}</code>
          引用命中意图
        </el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';

.hint {
  margin-left: 8px;
  color: #909399;
}

.empty-text {
  text-align: center;
  color: #909399;
  padding: 20px;
  font-size: 13px;
  background: #f5f7fa;
  border-radius: 4px;
}

.intent-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding-right: 8px;
}

.intent-cards {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.intent-card {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
  border: 1px solid #e4e7ed;
}

.intent-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.intent-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
}

.intent-desc-preview {
  font-size: 12px;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}

.intent-key-badge {
  font-family: 'Menlo', 'Monaco', monospace;
  font-size: 12px;
  color: #409eff;
  background: #ecf5ff;
  padding: 2px 8px;
  border-radius: 4px;
  flex-shrink: 0;
}

.intent-key-badge.invalid {
  color: #f56c6c;
  background: #fef0f0;
}

.intent-error {
  color: #f56c6c;
  font-size: 12px;
}

.regex-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.regex-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.output-variables-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.output-var-tag {
  display: flex;
  align-items: center;
  gap: 8px;
}

.output-var-desc {
  font-size: 12px;
  color: #909399;
}
</style>
