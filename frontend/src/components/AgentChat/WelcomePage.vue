<script setup lang="ts">
import { ChatDotRound } from '@element-plus/icons-vue'

defineProps<{
  agentName: string
  agentDescription?: string
  suggestedPrompts: string[]
}>()

const emit = defineEmits<{
  (e: 'selectPrompt', prompt: string): void
}>()
</script>

<template>
  <div class="welcome-page">
    <div class="welcome-content">
      <div class="welcome-icon">
        <el-icon :size="36">
          <ChatDotRound />
        </el-icon>
      </div>
      <h1 class="welcome-title">{{ agentName }}</h1>
      <p v-if="agentDescription" class="welcome-desc">{{ agentDescription }}</p>

      <div v-if="suggestedPrompts.length" class="prompts-section">
        <div class="prompts-label">试试这些</div>
        <div class="prompts-grid">
          <button
            v-for="(prompt, i) in suggestedPrompts"
            :key="i"
            class="prompt-chip"
            @click="emit('selectPrompt', prompt)"
          >
            <span class="prompt-icon">
              <el-icon :size="14"><ChatDotRound /></el-icon>
            </span>
            {{ prompt }}
          </button>
        </div>
      </div>
    </div>

    <p class="welcome-disclaimer">AI 生成内容仅供参考</p>
  </div>
</template>

<style scoped>
.welcome-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 24px 0;
}

.welcome-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 640px;
  width: 100%;
}

.welcome-icon {
  width: 64px;
  height: 64px;
  border-radius: 16px;
  background: linear-gradient(135deg, #e0e7ff, #c7d2fe);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 20px;
  color: #4f46e5;
}

.welcome-title {
  font-size: 24px;
  font-weight: 700;
  color: #1e293b;
  margin: 0 0 8px;
  letter-spacing: -0.02em;
}

.welcome-desc {
  font-size: 14px;
  color: #64748b;
  margin: 0 0 32px;
  text-align: center;
  line-height: 1.6;
  max-width: 480px;
}

.prompts-section {
  width: 100%;
}

.prompts-label {
  font-size: 12px;
  font-weight: 600;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 12px;
  text-align: center;
}

.prompts-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.prompt-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #fff;
  color: #334155;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
  max-width: 360px;
  text-align: left;
  line-height: 1.4;
}

.prompt-chip:hover {
  border-color: #818cf8;
  background: #eef2ff;
  color: #4f46e5;
}

.prompt-icon {
  flex-shrink: 0;
  color: #a5b4fc;
}

.prompt-chip:hover .prompt-icon {
  color: #6366f1;
}

.welcome-disclaimer {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 24px;
  flex-shrink: 0;
}
</style>
