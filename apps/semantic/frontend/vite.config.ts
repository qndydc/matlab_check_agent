/**
 * Description: Vite 开发服务器与生产构建配置。
 * 作用: 启用 React 编译，并把开发期 /api 请求代理到独立语义后端的 8000 端口。
 * 优点: 前端代码始终请求相对路径，开发时无需额外处理跨域地址。
 * 调用/被调用: 被 vite 和 pnpm dev/build 自动读取；调用 React Vite 插件。
 * 基础知识: 开发代理只在 Vite dev server 中生效，生产环境需要由部署层把前后端路由正确连接。
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: { dedupe: ['react', 'react-dom'] },
  server: {
    fs: { allow: ['.', '../../shared'] },
    host: '127.0.0.1',
    port: 5173,
    // 保留浏览器的 Host，让配置接口能校验同源请求（也支持自定义前端端口）。
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
  },
})
