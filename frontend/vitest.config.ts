// vitest.config.ts
// Separate from vite.config.ts to avoid interfering with the production build.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/__tests__/setup.ts'],
    include: ['src/__tests__/**/*.test.ts', 'src/__tests__/**/*.test.tsx'],
    pool: 'forks',
    coverage: {
      provider: 'v8',
      all: false,
      reporter: ['text', 'lcov', 'html'],
      reportsDirectory: './coverage',
      include: ['src/**/*.ts', 'src/**/*.tsx'],
      exclude: [
        'src/__tests__/**',
        'src/main.tsx',
        'src/vite-env.d.ts',
        'src-tauri/**',
        'src/renderer/**',
        'src/spatial/**',
        'src/interaction/**',
        'src/pages/ExplorerPage.tsx',
        'src/components/GraphVisualizer.tsx',
        'src/components/CrystalGraphTrace.tsx',
      ],
    },
  },
})

