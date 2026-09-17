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
            v-for="prompt in suggestedPrompts"
            :key="prompt"
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
  /* height:100% + 内部滚动兜底：welcome-wrapper 已钳制 height:100%（防进入
     抖动），此容器若内容意外超出（如超长描述）可内部滚动而非裁切 */
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 24px 0;
}

/* 内容主体：内容少时弹性居中；内容多（建议提示词放开上限）时顶部对齐、
   自然生长，由 .welcome-page 的 overflow-y:auto 内部滚动（welcome 已脱离
   el-scrollbar，此滚动不引发进入抖动）。flex-shrink:0 保证内容不被压缩
   （此前 flex:1 兄弟 disclaimer 挤压导致 chips 与免责声明重叠） */
.welcome-content {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  margin: auto 0;
  max-width: 640px;
  width: 100%;
  min-height: 0;
}

.welcome-icon {
  width: 64px;
  height: 64px;
  border-radius: 16px;
  background: var(--paper-warm);
  border: 1px solid var(--paper-line);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 20px;
  color: var(--vermilion);
}

.welcome-title {
  font-family: var(--font-serif);
  font-size: 24px;
  font-weight: 600;
  color: var(--paper-ink);
  margin: 0 0 8px;
  letter-spacing: -0.02em;
}

.welcome-desc {
  font-size: 14px;
  color: var(--paper-ink-3);
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
  color: var(--paper-ink-4);
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
  border: 1px solid var(--paper-line);
  border-radius: 12px;
  background: var(--paper-card);
  color: var(--paper-ink-2);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
  max-width: 360px;
  text-align: left;
  line-height: 1.4;
}

.prompt-chip:hover {
  border-color: var(--vermilion-line);
  background: var(--vermilion-soft);
  color: var(--vermilion);
}

.prompt-icon {
  flex-shrink: 0;
  color: var(--paper-ink-5);
}

.prompt-chip:hover .prompt-icon {
  color: var(--vermilion);
}

/* 移动端（与 AgentChat.vue 的 768px 断点同口径）：
   - 容器留白收窄（24px→16px，上 40→24）
   - 图标/标题降档（64→48 / 24→20）
   - chip 收紧（padding 8px 12px、字号 12.5px），小屏 2 列不溢出
   - MAX_VISIBLE_PROMPTS 逻辑层不动——8 个 chip 在 375px 下约 5-6 行，
     配合 welcome-page 内部滚动兜底不会撑破视口 */
@media (max-width: 768px) {
  .welcome-page {
    padding: 16px 16px 8px;
  }

  .welcome-icon {
    width: 48px;
    height: 48px;
    border-radius: 12px;
    margin-bottom: 12px;
  }

  .welcome-title {
    font-size: 20px;
    margin: 0 0 6px;
  }

  .welcome-desc {
    margin-bottom: 16px;
    font-size: 13px;
    line-height: 1.55;
  }

  .prompts-label {
    margin-bottom: 8px;
  }

  .prompt-chip {
    padding: 8px 12px;
    font-size: 12.5px;
    border-radius: 10px;
    max-width: 100%;
  }

  .welcome-disclaimer {
    margin-top: 12px;
    padding-bottom: 4px;
  }
}

.welcome-disclaimer {
  /* 跟随内容流：不做钉底（滚动容器内 flex:1 的兄弟会被压缩导致重叠），
     margin-top 保证与末行 chips 的最小间距；内容不足一屏时由
     .welcome-content 的 margin:auto 0 居中，disclaimer 自然贴在其后 */
  margin-top: 16px;
  padding-bottom: 12px;
  font-size: 11px;
  color: var(--paper-ink-4);
  flex-shrink: 0;
}
</style>
