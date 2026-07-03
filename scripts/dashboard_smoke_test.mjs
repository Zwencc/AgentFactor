import { createRequire } from 'node:module'

const require = createRequire(`${process.cwd()}/package.json`)
const { chromium } = require('playwright')

const baseUrl = process.env.ACD_BASE_URL || 'http://127.0.0.1:9889'
const headless = process.env.HEADLESS !== 'false'
const projectId = process.env.ACD_TEST_PROJECT || `e2e-${Date.now()}`

const failures = []

function fail(message) {
  failures.push(message)
  console.error(`FAIL ${message}`)
}

function ok(message) {
  console.log(`OK ${message}`)
}

async function expectVisible(page, locator, message) {
  try {
    await locator.waitFor({ state: 'visible', timeout: 8000 })
    ok(message)
  }
  catch (error) {
    fail(`${message}: ${error.message}`)
  }
}

async function clickIfVisible(locator, message) {
  try {
    await locator.waitFor({ state: 'visible', timeout: 5000 })
    await locator.click()
    ok(message)
    return true
  }
  catch (error) {
    fail(`${message}: ${error.message}`)
    return false
  }
}

const browser = await chromium.launch({ headless })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

page.on('pageerror', error => fail(`page error: ${error.message}`))
page.on('console', message => {
  if (message.type() === 'error')
    fail(`console error: ${message.text()}`)
})
page.on('response', response => {
  const url = response.url()
  if (response.status() >= 500)
    fail(`HTTP ${response.status()} ${url}`)
})

try {
  await page.goto(`${baseUrl}/`, { waitUntil: 'networkidle' })
  if (!page.url().includes('/admin'))
    fail(`root did not redirect to /admin, current URL: ${page.url()}`)
  else
    ok('root redirects to admin')

  await expectVisible(page, page.getByText('总览').first(), 'default Chinese dashboard renders')

  const pages = [
    ['/admin/overview', '总览'],
    ['/admin/tasks', '任务中心'],
    ['/admin/sessions', '终端会话'],
    ['/admin/prompts', '协作提示'],
    ['/admin/approvals', '审批'],
    ['/admin/context', '运行监控台'],
    ['/admin/work-graph', '工作图谱'],
    ['/admin/topology', '团队拓扑'],
    ['/admin/providers', '运行环境'],
    ['/admin/handbook', '操作手册'],
  ]

  for (const [path, title] of pages) {
    await page.goto(`${baseUrl}${path}`, { waitUntil: 'networkidle' })
    await expectVisible(page, page.getByText(title).first(), `deep link ${path} renders ${title}`)
  }

  await page.goto(`${baseUrl}/admin/overview`, { waitUntil: 'networkidle' })
  for (const title of ['任务中心', '终端会话', '协作提示', '审批', '运行监控台', '工作图谱', '团队拓扑', '运行环境', '操作手册']) {
    const clicked = await clickIfVisible(page.getByText(title).first(), `sidebar click ${title}`)
    if (clicked)
      await page.waitForLoadState('networkidle')
  }

  await page.goto(`${baseUrl}/admin/tasks`, { waitUntil: 'networkidle' })
  const projectButton = page.locator('button').filter({ hasText: /default|e2e-/ }).first()
  if (await clickIfVisible(projectButton, 'open project selector')) {
    await page.getByPlaceholder('Project ID').fill(projectId)
    await page.locator('button').filter({ hasText: `Use "${projectId}"` }).click()
    await expectVisible(page, page.getByText(projectId).first(), 'project selector accepts custom project')
  }

  await page.goto(`${baseUrl}/admin/tasks`, { waitUntil: 'networkidle' })
  if (await clickIfVisible(page.getByRole('button', { name: /选择|Browse/ }), 'open working directory picker')) {
    await expectVisible(page, page.getByText(/选择工作目录|Select working directory/), 'directory picker renders')
    await clickIfVisible(page.getByRole('button', { name: /使用当前目录|Use current/ }), 'use current directory')
    const directoryValue = await page.locator('input').nth(1).inputValue().catch(() => '')
    if (!directoryValue)
      fail('working directory was not populated after choosing current directory')
    else
      ok(`working directory populated: ${directoryValue}`)
  }

  await page.goto(`${baseUrl}/admin/work-graph`, { waitUntil: 'networkidle' })
  if (await clickIfVisible(page.getByRole('button', { name: /新建工作项|New item/ }), 'open work item create form')) {
    const title = `E2E work item ${Date.now()}`
    await page.getByPlaceholder(/标题|Title/).fill(title)
    await page.getByRole('button', { name: /^创建$|^Create$/ }).click()
    await expectVisible(page, page.getByText(title), 'created work item appears')
  }

  await page.goto(`${baseUrl}/admin/handbook`, { waitUntil: 'networkidle' })
  await expectVisible(page, page.getByText('操作手册').first(), 'handbook renders')
  await clickIfVisible(page.getByText('命令速查').first(), 'handbook section navigation')
  await expectVisible(page, page.getByText('CLI 命令速查').first(), 'handbook command reference visible')
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
