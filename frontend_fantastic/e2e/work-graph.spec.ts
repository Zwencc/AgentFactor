import { expect, test } from '@playwright/test'
import { mockConductorApi, setMockBlueprintHistory } from './helpers'

test.beforeEach(async ({ page }) => {
  await mockConductorApi(page)
  await page.goto('/admin/#/work-graph')
  await expect(page.getByRole('heading', { name: 'Work Graph' })).toBeVisible()
})

// ── Page structure ───────────────────────────────────────────────────

test('shows stat cards', async ({ page }) => {
  await expect(page.getByText('Open Items')).toBeVisible()
  await expect(page.getByText('Critical Path')).toBeVisible()
})

test('shows work item from mock data', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Set up project scaffolding' })).toBeVisible()
})

test('status filter select has all options', async ({ page }) => {
  const select = page.locator('select').first()
  await expect(select).toBeVisible()
  await expect(select.locator('option', { hasText: 'All statuses' })).toBeAttached()
  await expect(select.locator('option', { hasText: 'Ready' })).toBeAttached()
  await expect(select.locator('option', { hasText: 'In progress' })).toBeAttached()
  await expect(select.locator('option', { hasText: 'Done' })).toBeAttached()
})

// ── Auto-generate modal ──────────────────────────────────────────────

test('auto-generate button is visible', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Auto-generate' })).toBeVisible()
})

test('clicking auto-generate opens the config modal', async ({ page }) => {
  await page.getByRole('button', { name: 'Auto-generate' }).click()
  await expect(page.getByText('Auto-generate Work Graph')).toBeVisible()
  await expect(page.getByPlaceholder(/Describe your project/)).toBeVisible()
})

test('generate button is disabled when description is empty', async ({ page }) => {
  await page.getByRole('button', { name: 'Auto-generate' }).click()
  const generateBtn = page.getByRole('button', { name: 'Start generation' })
  await expect(generateBtn).toBeDisabled()
})

test('generate button enables after typing description', async ({ page }) => {
  await page.getByRole('button', { name: 'Auto-generate' }).click()
  await page.getByPlaceholder(/Describe your project/).fill('Build a task management API with REST endpoints')
  const generateBtn = page.getByRole('button', { name: 'Start generation' })
  await expect(generateBtn).toBeEnabled()
})

test('previous blueprint can be selected as generation baseline', async ({ page }) => {
  setMockBlueprintHistory([{
    terminal_id: 'term-history',
    project_id: 'default',
    status: 'ready',
    created_at: '2026-05-16T10:00:00Z',
    imported_at: null,
    meta: { title: 'Previous Plan' },
    items: [
      { id: 'A', title: 'Task Alpha', description: 'First task', category: 'feature', priority: 1, effort_hours: 2, depends_on: [], proof_requirements: [], acceptance_criteria: [], files_of_interest: [] },
    ],
    toml_content: '[meta]\ntitle = "Previous Plan"',
  }])

  await page.getByRole('button', { name: 'Auto-generate' }).click()
  await page.getByRole('button', { name: 'Advanced options' }).click()
  await expect(page.getByText('History baseline')).toBeVisible()
  await page.locator('select').filter({ has: page.getByRole('option', { name: /Previous Plan/ }) }).selectOption('term-history')
  await expect(page.getByRole('button', { name: 'Start generation' })).toBeEnabled()
})

test('modal has persona and provider selects', async ({ page }) => {
  await page.getByRole('button', { name: 'Auto-generate' }).click()
  await page.getByRole('button', { name: 'Advanced options' }).click()
  // The modal's persona select should contain the mocked 'conductor' persona
  await expect(page.getByRole('option', { name: 'conductor' })).toBeAttached()
  // Provider select should contain Claude Code
  await expect(page.getByRole('option', { name: 'Claude Code' })).toBeAttached()
})

test('MCP checkbox auto-syncs when provider changes', async ({ page }) => {
  await page.getByRole('button', { name: 'Auto-generate' }).click()
  await page.getByRole('button', { name: 'Advanced options' }).click()
  // Default provider (claude_code) has mcp_capable=true → checkbox should be checked
  const checkbox = page.locator('input[type="checkbox"]')
  await expect(checkbox).toBeChecked()
  // Switch to Codex (mcp_capable=false)
  const providerSelect = page.locator('select').filter({ has: page.getByRole('option', { name: 'Claude Code' }) })
  await providerSelect.selectOption('codex')
  await expect(checkbox).not.toBeChecked()
})

test('cancel button closes the modal', async ({ page }) => {
  await page.getByRole('button', { name: 'Auto-generate' }).click()
  await expect(page.getByText('Auto-generate Work Graph')).toBeVisible()
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByText('Auto-generate Work Graph')).not.toBeVisible()
})

test('clicking backdrop closes the modal', async ({ page }) => {
  await page.getByRole('button', { name: 'Auto-generate' }).click()
  await expect(page.getByText('Auto-generate Work Graph')).toBeVisible()
  // Click the dark overlay (fixed inset-0)
  await page.mouse.click(10, 10)
  await expect(page.getByText('Auto-generate Work Graph')).not.toBeVisible()
})

// ── Blueprint review modal ───────────────────────────────────────────

test('review modal shows imported items and select-all button', async ({ page }) => {
  // Arrange: mock generate + blueprint endpoints
  const BLUEPRINT = {
    terminal_id: 'term-abc',
    project_id: 'default',
    status: 'ready',
    created_at: '2026-05-01T00:00:00Z',
    meta: { title: 'Test Project' },
    items: [
      { id: 'A', title: 'Task Alpha', description: 'First task', category: 'feature', priority: 1, effort_hours: 2, depends_on: [], proof_requirements: [], acceptance_criteria: [], files_of_interest: [] },
      { id: 'B', title: 'Task Beta', description: 'Second task', category: 'feature', priority: 2, effort_hours: 4, depends_on: ['A'], proof_requirements: [], acceptance_criteria: [], files_of_interest: [] },
    ],
  }

  await page.route('**/proxy/work-graph/generate', route =>
    route.fulfill({ json: { terminal_id: 'term-abc', session_name: 'test-session' } }))
  await page.route('**/proxy/work-graph/blueprint/term-abc**', route =>
    route.fulfill({ json: BLUEPRINT }))

  await page.getByRole('button', { name: 'Auto-generate' }).click()
  await page.getByPlaceholder(/Describe your project/).fill('Build a task management API')
  await page.getByRole('button', { name: 'Start generation' }).click()

  // Background polling fires after BG_POLL_INTERVAL (10 s); allow up to 20 s
  await expect(page.getByText('Review Blueprint')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Task Alpha')).toBeVisible()
  await expect(page.getByText('Task Beta')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Select all' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Import selected' })).toBeVisible()
})
