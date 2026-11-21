/// <reference types="vitest/config" />
import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { connectSrcPolicy } from './src/connectCsp'

// 本番ビルドだけ、接続先を自分の HTTP API と Cognito に絞る CSP を index.html に埋め込む（src/connectCsp.ts）。
// 開発サーバーは HMR の WebSocket を使うため対象にしない
function connectCsp(mode: string): Plugin {
  return {
    name: 'registry-portal-connect-csp',
    apply: 'build',
    transformIndexHtml() {
      const policy = connectSrcPolicy(loadEnv(mode, process.cwd(), 'VITE_'))
      return policy === null
        ? []
        : [{ tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy', content: policy }, injectTo: 'head-prepend' }]
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react(), connectCsp(mode)],
  server: {
    port: 5173,
    strictPort: true,
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
}))
