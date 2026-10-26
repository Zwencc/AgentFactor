import { createRequire } from 'node:module'

const require = createRequire(`${process.cwd()}/package.json`)
const { chromium } = require('playwright')

const baseUrl = process.env.ACD_BASE_URL || 'http://127.0.0.1:9889'
const headless = process.env.HEADLESS !== 'false'
const failures = []

function fail(message) {
  failures.push(message)
  console.error(`FAIL ${message}`)
}

function ok(message) {
  console.log(`OK ${message}`)
}

async function expectVisible(locator, message) {
  try {
    await locator.waitFor({ state: 'visible', timeout: 8000 })
    ok(message)
  }
  catch (error) {
    fail(`${message}: ${error.message}`)
  }
}

const browser = await chromium.launch({ headless })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

page.on('pageerror', error => fail(`page error: ${error.message}`))
page.on('console', (message) => {
  if (message.type() === 'error')
    fail(`console error: ${message.text()}`)
})
page.on('response', (response) => {
  if (response.status() >= 500)
    fail(`HTTP ${response.status()} ${response.url()}`)
})

try {
  await page.goto(`${baseUrl}/`, { waitUntil: 'networkidle' })
  if (!page.url().includes('/admin/#/security'))
    fail(`root did not open the security overview: ${page.url()}`)
  else
    ok('root opens the security overview')

  const pages = [
    ['/admin/#/security', /安全总览|Security Overview/],
    ['/admin/#/security/audit', /安全审计|Security Audit/],
    ['/admin/#/security/mcp', /MCP 网关|MCP Gateway/],
    ['/admin/#/security/sandboxes', /Agent 沙箱|Agent Sandboxes/],
  ]

  for (const [path, title] of pages) {
    await page.goto(`${baseUrl}${path}`, { waitUntil: 'networkidle' })
    await expectVisible(page.getByRole('heading', { name: title }), `${path} renders`)
  }

  await page.goto(`${baseUrl}/admin/#/work-graph`, { waitUntil: 'networkidle' })
  await page.waitForURL('**/admin/#/security')
  if (!page.url().endsWith('/#/security'))
    fail(`legacy route did not redirect to security overview: ${page.url()}`)
  else
    ok('legacy orchestration route redirects')
}
finally {
  await browser.close()
}

if (failures.length) {
  console.error(`\n${failures.length} dashboard smoke failure(s):`)
  for (const item of failures)
    console.error(`- ${item}`)
  process.exit(1)
}

console.log('\nDashboard smoke test passed.')
