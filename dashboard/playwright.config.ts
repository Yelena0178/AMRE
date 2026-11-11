import { defineConfig } from '@playwright/test'

const port = Number(process.env.AMRE_DASHBOARD_PORT ?? 4178)
const baseURL = 'http://127.0.0.1:' + port
export default defineConfig({
  testDir: './tests',
  testMatch: '**/browser.spec.ts',
  workers: 1,
  use: { baseURL, channel: process.env.AMRE_DASHBOARD_BROWSER },
  webServer: {
    command: 'node tests/serve.mjs',
    url: baseURL,
    reuseExistingServer: false,
  },
})
