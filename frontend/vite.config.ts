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
  build: {
    rollupOptions: {
      output: {
        // 末尾固定后缀防止哈希随机撞广告拦截规则（如 *_ad.js$ 把 _aD.js 结尾的 chunk 全拦）
        chunkFileNames: 'assets/[name]-[hash].chunk.js',
        entryFileNames: 'assets/[name]-[hash].entry.js'
      }
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
