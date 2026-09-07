import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: { dedupe: ['react', 'react-dom'] },
  server: {
    fs: { allow: ['.', '../../shared'] },
    host: '127.0.0.1',
    port: 5174,
    // 保留浏览器的 Host，让配置接口能校验同源请求（也支持自定义前端端口）。
    proxy: { '/api': { target: 'http://127.0.0.1:8001', changeOrigin: false } },
  },
})
