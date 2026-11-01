import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base 用相对路径，兼容 GitHub Pages 项目子路径（/DataAgent/）与本地 preview
export default defineConfig({
  base: './',
  plugins: [react()],
})
