// Vue 应用入口：挂载根组件到 index.html 的 #root 节点
import { createApp } from 'vue'
import './index.css'   // 全局基础样式
import App from './App.vue'  // 唯一的根组件（整个界面都在这里）

createApp(App).mount('#root')
