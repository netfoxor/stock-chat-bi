import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * 生产构建若在 Rollup 「rendering chunks」阶段长时间无输出：
 * - 常为 **内存峰值**（大依赖树 + sourcemap）；加入 CodeMirror 6 会因 @codemirror/@lezer 大幅增加模块数而触发。
 * - 低配 Docker：**NODE_OPTIONS=--max-old-space-size** + **rollup maxParallelFileOps** 节流 是社区常用稳定手段（Vite Issue #2433 等）。
 */
export default defineConfig({
  plugins: [react()],
  build: {
    sourcemap: false,
    reportCompressedSize: false,
    minify: "esbuild",
    rollupOptions: {
      maxParallelFileOps: 2,
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (
            id.includes("@codemirror") ||
            id.includes("@uiw/react-codemirror") ||
            id.includes("@lezer")
          ) {
            return "codemirror";
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 与同域 `/api` 一致，开发时走后端 localhost:8000
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
