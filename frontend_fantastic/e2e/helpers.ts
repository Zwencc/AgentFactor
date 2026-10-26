import type { Page, Route } from '@playwright/test'

function apiPath(url: string) {
  const pathname = new URL(url).pathname.replace(/\/+$/, '')
  return pathname.startsWith('/proxy/') ? pathname.slice('/proxy'.length) : pathname
}

export async function mockSecurityApi(page: Page) {
  await page.addInitScript(() => localStorage.setItem('agentfactor-language', 'en'))

  const handler = (route: Route) => {
    const pathname = apiPath(route.request().url())

    if (pathname === '/security/v1alpha1/overview') {
      return route.fulfill({
        json: {
          endpoints: 0,
          integrations: 0,
          degraded_integrations: 0,
          events: 0,
          decisions: {},
          action_effects: { record: 0, remind: 0, block: 0 },
          reminders: 0,
          blocked: 0,
        },
      })
    }

    return route.fulfill({ json: [] })
  }

  await page.route('**/proxy/**', handler)
  await page.route('**/security/v1alpha1/**', handler)
}
