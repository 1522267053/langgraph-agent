/**
 * 统一通知中心 Store
 * @description 未读数管理 + WebSocket 实时推送联动
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { notificationApi } from '@/api/notification'

export const useNotificationStore = defineStore('notification', () => {
  const unreadCount = ref(0)
  /** 最新一条 WS 推送（通知页 watch 它实时刷新列表） */
  const lastNotification = ref<Record<string, unknown> | null>(null)

  /** 拉取未读数（登录后调用） */
  async function fetchUnreadCount(): Promise<void> {
    try {
      const res = await notificationApi.getUnreadCount()
      unreadCount.value = res.data.data?.count ?? 0
    } catch {
      // 静默失败（未登录等）
    }
  }

  /** WebSocket 推送联动：直接采用后端推送的最新未读数 */
  function onWsNotification(payload: { unread_count?: number }): void {
    if (typeof payload.unread_count === 'number') {
      unreadCount.value = payload.unread_count
    } else {
      unreadCount.value++
    }
    lastNotification.value = { ...payload, _ts: Date.now() }
  }

  /** 清零（全部已读后调用） */
  function resetCount(): void {
    unreadCount.value = 0
  }

  return { unreadCount, lastNotification, fetchUnreadCount, onWsNotification, resetCount }
})
