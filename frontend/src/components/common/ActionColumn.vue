<template>
  <div class="action-column">
    <div class="desktop-actions">
      <el-tooltip
        v-for="action in visibleActions"
        :key="action.key"
        :content="action.label"
        placement="top"
      >
        <el-button
          class="action-btn"
          :class="action.btnClass"
          :icon="action.icon"
          circle
          :disabled="action.disabled"
          @click="emit('action', action.key)"
        />
      </el-tooltip>
    </div>
    <div class="mobile-actions">
      <el-dropdown trigger="click" @command="emit('action', $event as string)">
        <el-button class="action-btn action-more" :icon="MoreFilled" circle />
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item
              v-for="action in visibleActions"
              :key="action.key"
              :command="action.key"
              :disabled="action.disabled"
              :class="{ 'is-danger': action.danger }"
            >
              <el-icon v-if="action.icon" style="margin-right: 6px">
                <component :is="action.icon" />
              </el-icon>
              {{ action.label }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { MoreFilled } from '@element-plus/icons-vue'
import type { Component } from 'vue'

export interface ActionItem {
  key: string
  label: string
  icon?: Component
  btnClass?: string
  visible?: boolean
  disabled?: boolean
  danger?: boolean
}

const props = withDefaults(defineProps<{ actions: ActionItem[] }>(), {
  actions: () => []
})

const emit = defineEmits<{
  (e: 'action', key: string): void
}>()

const visibleActions = computed(() => props.actions.filter(a => a.visible !== false))
</script>

<style scoped>
.action-column {
  display: inline-flex;
  align-items: center;
}

.desktop-actions {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.mobile-actions {
  display: none;
}

@media (max-width: 767px) {
  .desktop-actions {
    display: none;
  }

  .mobile-actions {
    display: inline-flex;
  }
}
</style>

<style>
.el-dropdown-menu__item.is-danger {
  color: #dc2626;
}

.el-dropdown-menu__item.is-danger:hover {
  background-color: #fef2f2;
  color: #dc2626;
}

.el-button.action-btn.action-more {
  --el-button-bg-color: #f8fafc;
  --el-button-border-color: transparent;
  --el-button-text-color: #475569;
  --el-button-hover-bg-color: #1e293b;
  --el-button-hover-border-color: #1e293b;
  --el-button-hover-text-color: #fff;
}
</style>
