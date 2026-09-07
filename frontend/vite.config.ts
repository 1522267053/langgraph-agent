import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
const ip = '127.0.0.1'
const port = 8800
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
    port: 3001,
    proxy: {
      '/api': {
        target: `http://${ip}:${port}`,
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
      '/uploads': {
        target: `http://${ip}:${port}`,
        changeOrigin: true,
      },
      '/ws': {
        target: `ws://${ip}:${port}`,
        ws: true,
        changeOrigin: true,
      },
    },
  }
})
