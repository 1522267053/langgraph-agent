import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: 'src/components.d.ts'
    })
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': {
        target: 'https://agent.wuguobang.site',
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
      '/uploads': {
        target: 'https://agent.wuguobang.site',
        changeOrigin: true,
      },
      '/ws': {
        target: 'wss://agent.wuguobang.site',
        ws: true,
        changeOrigin: true,
      },
    },
  }
})
