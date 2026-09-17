<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChatDotRound, Refresh } from '@element-plus/icons-vue'

const props = defineProps<{
  agentName: string
  agentDescription?: string
  suggestedPrompts: string[]
}>()

const emit = defineEmits<{
  (e: 'selectPrompt', prompt: string): void
}>()

/**
 * 建议提示词最多同屏 8 个：welcome-wrapper 已钳制 height:100%（防进入抖动），
 * 提示词过多会撑破视口被裁切。超限时只随机展示一批，点「换一批」再随机换一组
 * （Fisher-Yates 局部洗牌，取前 8）。≤8 个全量展示不显示换一批。
 */
const MAX_VISIBLE_PROMPTS = 8

const visiblePrompts = computed(() => {
  if (props.suggestedPrompts.length <= MAX_VISIBLE_PROMPTS) {
    return props.suggestedPrompts
  }
  return shuffledPrompts.value
})

const shuffledPrompts = ref<string[]>([])
const needsShuffle = computed(() => props.suggestedPrompts.length > MAX_VISIBLE_PROMPTS)

function shufflePrompts(): void {
  const pool = [...props.suggestedPrompts]
  // Fisher-Yates：从尾部向前，每步与随机前位交换，取前 8 即随机一批
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[pool[i], pool[j]] = [pool[j], pool[i]]
  }
  shuffledPrompts.value = pool.slice(0, MAX_VISIBLE_PROMPTS)
}

if (needsShuffle.value) shufflePrompts()
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

      <div v-if="visiblePrompts.length" class="prompts-section">
        <div class="prompts-label">试试这些</div>
        <div class="prompts-grid">
          <button
            v-for="(prompt, i) in visiblePrompts"
            :key="prompt"
            class="prompt-chip"
            @click="emit('selectPrompt', prompt)"
          >
            <span class="prompt-icon">
              <el-icon :size="14"><ChatDotRound /></el-icon>
            </span>
            {{ prompt }}
          </button>
          <button
            v-if="needsShuffle"
            class="prompt-chip prompt-shuffle"
            title="换一批建议"
            @click="shufflePrompts"
          >
            <span class="prompt-icon">
              <el-icon :size="14"><Refresh /></el-icon>
            </span>
            换一批
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

/* 内容主体弹性撑满并自身居中，免责声明钉底 */
.welcome-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
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

/* 「换一批」功能按钮：虚线边框与建议 chip 区分，弱化视觉权重 */
.prompt-chip.prompt-shuffle {
  border-style: dashed;
  background: transparent;
  color: var(--paper-ink-4);
}
.prompt-chip.prompt-shuffle:hover {
  border-color: var(--vermilion-line);
  background: var(--vermilion-soft);
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
  font-size: 11px;
  color: var(--paper-ink-4);
  margin-top: 24px;
  flex-shrink: 0;
}
</style>
