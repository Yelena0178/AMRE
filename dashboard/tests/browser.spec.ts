import { expect, test } from '@playwright/test'

test('opens a real AND/OR graph and inspects assumptions and evidence', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Research overview' })).toBeVisible()
  await expect(page.locator('.graph-canvas canvas').first()).toBeVisible()
  await expect(page.getByText('AND · all required lemmas')).toBeVisible()
  await expect(page.getByText('OR · alternative routes')).toBeVisible()
  await page.getByRole('button', { name: /^A proof route$/i }).click()
  const inspector = page.getByRole('complementary', { name: 'Node inspector' })
  await expect(inspector.getByRole('heading', { name: 'A', exact: true })).toBeVisible()
  await expect(inspector.getByRole('heading', { name: 'Assumptions' })).toBeVisible()
  await expect(inspector.getByRole('heading', { name: 'Evidence', exact: true })).toBeVisible()
  await inspector.getByRole('button', { name: 'L1', exact: true }).first().click()
  await expect(inspector.getByRole('heading', { name: 'L1', exact: true })).toBeVisible()
  await page.getByLabel('Filter node type').selectOption('LEMMA')
  await page.getByRole('button', { name: 'Zoom in' }).click()
  await page.getByRole('button', { name: 'Sessions', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Deep research sessions' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Route and checkpoint history' })).toBeVisible()
  await page.getByRole('button', { name: 'Evidence', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Research artifacts' })).toBeVisible()
})

test('handles broken links and invalid math without loading external content', async ({ page }) => {
  await page.route('**/research-state.json', async (route) => {
    const reply = await route.fetch()
    const state = await reply.json()
    state.edges.push({ source: 'P-T', target: 'missing', relation: 'REQUIRES_ALL' })
    state.nodes.find((item: { id: string }) => item.id === 'P-T').statement =
      'Review \\( \\badcommand{q} \\).'
    await route.fulfill({ json: state })
  })
  await page.goto('/')
  await expect(page.getByText('Some graph links reference missing nodes.')).toBeVisible()
  await expect(page.getByTitle('Could not render this formula')).toBeVisible()
  await expect(page.locator('.math img')).toHaveCount(0)
})

test('reports an unsupported schema', async ({ page }) => {
  await page.route('**/research-state.json', (route) => route.fulfill({ json: { schema_version: 9 } }))
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('Unsupported research snapshot schema')
})
