import type { Page, Route } from '@playwright/test'

const MOCK_PROVIDERS = [
  { key: 'claude_code', label: 'Claude Code', binary: 'claude', available: true, reason: '', mcp_capable: true },
  { key: 'codex', label: 'OpenAI Codex', binary: 'codex', available: false, reason: 'not found', mcp_capable: false },
]

const MOCK_PERSONAS = [
  { name: 'conductor', description: 'Default planner', default_provider: 'claude_code', source: 'store', scope: 'global', tags: [] },
]

const MOCK_DASHBOARD_STATE = {
  health: { status: 'ok', terminals: { total: 0, ready: 0, running: 0, completed: 0, error: 0 } },
  sessions: [],
  pending_prompt_count: 0,
  prompt_items: [],
  pending_approvals: [],
  approvals: [],
  approvals_summary: { pending: 0, approved: 0, denied: 0, total: 0 },
  providers: MOCK_PROVIDERS,
}

const MOCK_WORK_GRAPH = {
  project_id: 'default',
  work_items: [
    {
      id: 'item-001',
      project_id: 'default',
      title: 'Set up project scaffolding',
      description: 'Initialize the project structure and tooling.',
      type: 'feature',
      status: 'ready',
      priority: 1,
      owner_terminal_id: null,
      acceptance_criteria: [],
      files_of_interest: [],
      proof_requirements: null,
      complexity: 1,
      created_at: '2026-05-01T00:00:00Z',
      updated_at: '2026-05-01T00:00:00Z',
    },
  ],
  edges: [],
  critical_path: ['item-001'],
  scope_conflicts: [],
}

let mockBlueprintHistory: unknown[] = []

export function setMockBlueprintHistory(items: unknown[]) {
  mockBlueprintHistory = items
}

function apiPath(url: string) {
  const pathname = new URL(url).pathname.replace(/\/+$/, '')
  return pathname.startsWith('/proxy/') ? pathname.slice('/proxy'.length) : pathname
}

export async function mockConductorApi(page: Page) {
  // Force English UI so tests use predictable text
  await page.addInitScript(() => localStorage.setItem('agentfactor-language', 'en'))
  mockBlueprintHistory = []

  const handler = (route: Route) => {
    const pathname = apiPath(route.request().url())

    if (pathname === '/dashboard/state')
      return route.fulfill({ json: MOCK_DASHBOARD_STATE })
    if (pathname === '/projects')
      return route.fulfill({ json: [{ id: 'default', root_directory: null }] })
    if (pathname === '/personas')
      return route.fulfill({ json: MOCK_PERSONAS })
    if (pathname === '/providers/health')
      return route.fulfill({ json: MOCK_PROVIDERS })
    if (/^\/projects\/[^/]+\/work-graph\/blueprints$/.test(pathname))
      return route.fulfill({ json: mockBlueprintHistory })
    if (/^\/projects\/[^/]+\/work-graph$/.test(pathname))
      return route.fulfill({ json: MOCK_WORK_GRAPH })
    if (/^\/projects\/[^/]+\/work-items$/.test(pathname))
      return route.fulfill({ json: [] })
    if (/^\/work-items\/[^/]+\/proof-windows$/.test(pathname))
      return route.fulfill({ json: [] })
    if (pathname === '/events')
      return route.fulfill({ json: [] })

    return route.fulfill({ status: 200, body: '[]', contentType: 'application/json' })
  }

  await page.route('**/proxy/**', handler)
  await page.route('**/dashboard/state*', handler)
  await page.route('**/projects/**', handler)
  await page.route('**/personas', handler)
  await page.route('**/providers/health', handler)
  await page.route('**/work-items/**', handler)
  await page.route('**/events*', handler)
}
