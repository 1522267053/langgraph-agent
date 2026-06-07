import { ref } from 'vue'
import { defineStore } from 'pinia'
import { marketplaceApi, type MarketplaceStatus } from '@/api/marketplace'

export const useMarketplaceStore = defineStore('marketplace', () => {
  const serverUrl = ref('')
  const connected = ref(false)
  const loading = ref(false)

  async function loadStatus() {
    try {
      const res = await marketplaceApi.getStatus()
      const data = res.data.data as MarketplaceStatus
      serverUrl.value = data?.server_url || ''
      connected.value = data?.connected || false
    } catch {
      // ignore
    }
  }

  async function saveConfig(url: string) {
    loading.value = true
    try {
      const res = await marketplaceApi.saveConfig(url)
      const data = res.data.data as { connected: boolean }
      connected.value = data?.connected || false
      serverUrl.value = url
      return { data, msg: res.data.msg }
    } finally {
      loading.value = false
    }
  }

  async function disconnect() {
    try {
      await marketplaceApi.disconnect()
      connected.value = false
    } catch {
      // ignore
    }
  }

  return { serverUrl, connected, loading, loadStatus, saveConfig, disconnect }
})
