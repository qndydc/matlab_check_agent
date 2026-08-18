/**
 * Description: React 前端的浏览器启动入口。
 * 作用: 加载全局样式，并把根组件 App 挂载到 index.html 的 #root 节点。
 * 优点: 启动职责单一，业务逻辑全部留在独立组件中。
 * 调用/被调用: 被 index.html 的 module script 调用；调用 ReactDOM、App.tsx 和 styles.css。
 * 基础知识: createRoot 创建 React 18 根节点；StrictMode 在开发期帮助发现不安全的副作用。
 */
import React from 'react'
import ReactDOM from 'react-dom/client'
import '@xyflow/react/dist/style.css'
import './styles.css'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><App /></React.StrictMode>,
)
