import { readFileSync } from 'node:fs'
import { defineConfig, type Plugin } from 'vite'

function researchSnapshot(): Plugin {
  const filename = process.env.AMRE_DASHBOARD_SNAPSHOT
  const source = filename
    ? readFileSync(filename, 'utf8')
    : '{"schema_version":1,"problems":[],"nodes":[],"edges":[]}'

  return {
    name: 'amre-public-snapshot',
    configureServer(server) {
      server.middlewares.use('/research-state.json', (_request, response) => {
        response.setHeader('Content-Type', 'application/json; charset=utf-8')
        response.end(source)
      })
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'research-state.json', source })
    },
  }
}

export default defineConfig({
  base: './',
  publicDir: false,
  envDir: false,
  plugins: [researchSnapshot()],
  build: {
    rollupOptions: { output: { manualChunks: { graph: ['cytoscape'], math: ['katex'] } } },
  },
})
