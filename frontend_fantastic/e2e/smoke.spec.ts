import { expect, test } from '@playwright/test'
import { mockConductorApi } from './helpers'

test.beforeEach(async ({ page }) => {
  await mockConductorApi(page)
})

test('work graph page loads', async ({ page }) => {
  await page.goto('/admin/#/work-graph')
  await expect(page.getByRole('heading', { name: 'Work Graph' })).toBeVisible()
  await expect(page.locator('select').first()).toBeVisible()
})

test('overview page loads', async ({ page }) => {
  await page.goto('/admin/#/overview')
  await page.waitForLoadState('networkidle')
  // Page should render without a full-screen error
  await expect(page.locator('body')).not.toBeEmpty()
})

test('sessions page loads', async ({ page }) => {
  await page.goto('/admin/#/sessions')
  await page.waitForLoadState('networkidle')
  await expect(page.locator('body')).not.toBeEmpty()
})

test('approvals page loads', async ({ page }) => {
  await page.goto('/admin/#/approvals')
  await page.waitForLoadState('networkidle')
  await expect(page.locator('body')).not.toBeEmpty()
})
