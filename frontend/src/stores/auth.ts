import { ref } from 'vue'
import { defineStore } from 'pinia'
import type { AuthCheckData } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const currentUser = ref<string | null>(null)

  function applyCheck(data?: AuthCheckData): void {
    currentUser.value = data?.authenticated ? (data.username ?? null) : null
  }

  function clear(): void {
    currentUser.value = null
  }

  return { currentUser, applyCheck, clear }
})
