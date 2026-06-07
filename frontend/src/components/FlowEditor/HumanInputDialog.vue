<script setup lang="ts">
import { ref } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import { formatToolArgs } from '@/utils/format'

interface ConversationMessage {
  role: string
  content: string
  name?: string
  tool_calls?: Array<{ name: string; args: Record<string, unknown>; id?: string }>
}

defineProps<{
  visible: boolean
  question: string
  context: string
  messages: ConversationMessage[]
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'submit', value: string): void
  (e: 'cancel'): void
}>()

const inputValue = ref('')

function getRoleLabel(role: string): string {
  const labels: Record<string, string> = {
    system: '系统',
    user: '用户',
    assistant: 'AI',
    tool: '工具'
  }
  return labels[role] || role
}

function handleSubmit(): void {
  if (!inputValue.value.trim()) return
  emit('submit', inputValue.value)
}

function handleOpen(): void {
  inputValue.value = ''
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    title="需要您的输入"
    width="600px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
    @update:model-value="emit('update:visible', $event)"
    @open="handleOpen"
  >
    <div class="human-input-content">
      <div class="question">
        <el-icon style="margin-right: 8px; color: #e6a23c">
          <QuestionFilled />
        </el-icon>
        {{ question }}
      </div>

      <div v-if="messages.length > 0 || context" class="context-panel">
        <div class="context-label">上下文与对话历史：</div>

        <div v-if="messages.length > 0" class="history-messages">
          <div
            v-for="(msg, index) in messages"
            :key="index"
            :class="['message-item', `message-${msg.role}`]"
          >
            <span class="message-role">
              {{ getRoleLabel(msg.role) }}
              <template v-if="msg.name">({{ msg.name }})</template>
            </span>
            <div class="message-content">
              <template v-if="msg.tool_calls && msg.tool_calls.length > 0">
                <div class="tool-calls">
                  <div
                    v-for="(tc, tcIndex) in msg.tool_calls"
                    :key="tcIndex"
                    class="tool-call-item"
                  >
                    <div class="tool-call-name">🔧 {{ tc.name }}</div>
                    <pre v-if="tc.args && Object.keys(tc.args).length > 0" class="tool-call-args">{{
                      formatToolArgs(tc.args, 200)
                    }}</pre>
                  </div>
                </div>
                <div v-if="msg.content" class="tool-call-content">{{ msg.content }}</div>
              </template>
              <template v-else>{{ msg.content }}</template>
            </div>
          </div>
        </div>

        <div v-if="context" class="context-text">{{ context }}</div>
      </div>
      <el-input
        v-model="inputValue"
        type="textarea"
        :rows="4"
        placeholder="请输入您的回答..."
        @keydown.enter.ctrl="handleSubmit"
      />
    </div>
    <template #footer>
      <div style="display: flex; justify-content: space-between; width: 100%">
        <el-button @click="emit('cancel')">取消执行</el-button>
        <el-button type="primary" :loading="loading" @click="handleSubmit">提交并继续</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.human-input-content {
  padding: 10px 0;
}

.human-input-content .question {
  font-size: 16px;
  font-weight: 500;
  margin-bottom: 16px;
  display: flex;
  align-items: flex-start;
}

.human-input-content .context-panel {
  background: #f5f7fa;
  border-radius: 6px;
  margin-bottom: 16px;
  max-height: 300px;
  overflow-y: auto;
}

.human-input-content .context-label {
  font-size: 12px;
  color: #909399;
  padding: 8px 12px 4px;
  border-bottom: 1px solid #ebeef5;
}

.human-input-content .history-messages {
  padding: 8px 12px;
}

.human-input-content .context-text {
  font-size: 14px;
  color: #606266;
  white-space: pre-wrap;
  padding: 8px 12px;
  border-top: 1px solid #ebeef5;
}

.human-input-content .message-item {
  margin-bottom: 10px;
  padding: 8px;
  border-radius: 4px;
}

.human-input-content .message-item:last-child {
  margin-bottom: 0;
}

.human-input-content .message-system {
  background: #f0f9eb;
}

.human-input-content .message-user {
  background: #ecf5ff;
}

.human-input-content .message-assistant {
  background: #fef0f0;
}

.human-input-content .message-tool {
  background: #fdf6ec;
}

.human-input-content .message-role {
  font-size: 12px;
  font-weight: 500;
  color: #606266;
  margin-bottom: 4px;
  display: block;
}

.human-input-content .message-content {
  font-size: 13px;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
}

.human-input-content .tool-calls {
  margin-bottom: 8px;
}

.human-input-content .tool-call-item {
  background: rgba(0, 0, 0, 0.03);
  border-radius: 4px;
  padding: 6px 8px;
  margin-bottom: 6px;
}

.human-input-content .tool-call-item:last-child {
  margin-bottom: 0;
}

.human-input-content .tool-call-name {
  font-weight: 500;
  color: #409eff;
  margin-bottom: 4px;
}

.human-input-content .tool-call-args {
  margin: 0;
  padding: 6px;
  background: #f5f5f5;
  border-radius: 4px;
  font-size: 12px;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 120px;
  overflow-y: auto;
}

.human-input-content .tool-call-content {
  padding-top: 8px;
  border-top: 1px dashed #dcdfe6;
}
</style>
