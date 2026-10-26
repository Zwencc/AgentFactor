import { expect, test } from '@playwright/test'
import { mockSecurityApi } from './helpers'

test.beforeEach(async ({ page }) => {
  await mockSecurityApi(page)
})

test('root opens the security overview', async ({ page }) => {
  await page.goto('/admin/')
  await expect(page).toHaveURL(/#\/security$/)
  await expect(page.getByRole('heading', { name: 'Security Overview' })).toBeVisible()
})

test('security audit combines the security activity sources', async ({ page }) => {
  await page.goto('/admin/#/security/audit')
  await expect(page.getByRole('heading', { name: 'Security Audit' })).toBeVisible()
  await expect(page.getByText('Gateway decisions', { exact: true })).toBeVisible()
  await expect(page.getByText('All sources', { exact: true })).toBeVisible()
})

test('MCP Gateway and Agent Sandboxes load', async ({ page }) => {
  await page.goto('/admin/#/security/mcp')
  await expect(page.getByRole('heading', { name: 'MCP Gateway' })).toBeVisible()

  await page.goto('/admin/#/security/sandboxes')
  await expect(page.getByRole('heading', { name: 'Agent Sandboxes' })).toBeVisible()
})

test('legacy orchestration routes redirect to security overview', async ({ page }) => {
  await page.goto('/admin/#/work-graph')
  await expect(page).toHaveURL(/#\/security$/)
  await expect(page.getByRole('heading', { name: 'Security Overview' })).toBeVisible()
})
