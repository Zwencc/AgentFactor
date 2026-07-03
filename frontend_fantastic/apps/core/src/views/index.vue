<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { DashboardState, EventLogItem, WorkItem } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'ConductorOverview',
})

const router = useRouter()
const { text } = useLanguage()
const loading = ref(false)
const state = ref<DashboardState | null>(null)
const workItems = ref<WorkItem[]>([])
const events = ref<EventLogItem[]>([])
let refreshTimer: number | undefined

const terminalMetrics = computed(() => state.value?.health.terminals ?? {
  total: 0,
  ready: 0,
  running: 0,
  completed: 0,
  error: 0,
})

const sessions = computed(() => state.value?.sessions ?? [])

const completionRate = computed(() => {
  const total = workItems.value.length
  if (!total) return null
  const done = workItems.value.filter(i => i.status === 'done').length
  return { total, done, pct: Math.round((done / total) * 100) }
})

const workStatusGroups = computed(() => {
  const items = workItems.value
  return [
    { key: 'done', label: text('Done', '已完成'), count: items.filter(i => i.status === 'done').length, color: 'bg-emerald-500', text: 'text-emerald-700' },
    { key: 'in_progress', label: text('In progress', '进行中'), count: items.filter(i => i.status === 'in_progress').length, color: 'bg-blue-500', text: 'text-blue-700' },
    { key: 'needs_verification', label: text('Needs verify', '待验证'), count: items.filter(i => i.status === 'needs_verification').length, color: 'bg-amber-500', text: 'text-amber-700' },
    { key: 'blocked', label: text('Blocked', '已阻塞'), count: items.filter(i => i.status === 'blocked').length, color: 'bg-red-500', text: 'text-red-600' },
    { key: 'ready', label: text('Ready', '就绪'), count: items.filter(i => i.status === 'ready').length, color: 'bg-slate-400', text: 'text-muted-foreground' },
  ]
})

onMounted(async () => {
  await loadAll()
  refreshTimer = window.setInterval(() => {
    void loadAll(true).catch(() => {})
  }, 8000)
})

onBeforeUnmount(() => {
  if (refreshTimer)
    window.clearInterval(refreshTimer)
})

async function loadAll(silent = false) {
  loading.value = !silent
  try {
    const [, workResult, eventResult] = await Promise.allSettled([
      apiConductor.dashboardState(silent).then(d => { state.value = d }),
      apiConductor.workItems('default'),
      apiConductor.events({ limit: 12 }, true),
    ])
    if (workResult.status === 'fulfilled')
      workItems.value = workResult.value
    if (eventResult.status === 'fulfilled')
      events.value = eventResult.value
  }
  finally {
    loading.value = false
  }
}

function terminalDot(status: string) {
  const map: Record<string, string> = {
    RUNNING: 'bg-blue-500',
    READY: 'bg-green-500',
    COMPLETED: 'bg-muted-foreground/40',
    ERROR: 'bg-red-500',
  }
  return map[status] ?? 'bg-muted-foreground/40'
}

function terminalBadge(status: string) {
  const map: Record<string, string> = {
    RUNNING: 'border-blue-400/50 bg-blue-500/10 text-blue-700',
    READY: 'border-green-400/50 bg-green-500/10 text-green-700',
    COMPLETED: 'border-muted bg-muted/30 text-muted-foreground',
    ERROR: 'border-red-400/50 bg-red-500/10 text-red-600',
  }
  return map[status] ?? 'border-muted bg-muted/30 text-muted-foreground'
}

function eventTypeLabel(type: string) {
  const labels: Record<string, string> = {
    ERROR_OBSERVED: text('Error', '错误'),
    TEST_RESULT: text('Test', '测试'),
    GIT_ACTION: text('Git', 'Git'),
    FILE_WRITE: text('File', '文件'),
    COMPLETION_SIGNAL: text('Completed', '完成'),
    COMPACTION_NEEDED: text('Compact', '待压缩'),
    CONTEXT_LOSS_SIGNAL: text('Loss', '上下文丢失'),
    BLOCKER_SIGNAL: text('Blocked', '任务阻塞'),
    PROGRESS: text('Progress', '进展'),
    DECISION: text('Decision', '决策'),
    ARCHITECTURE_NOTE: text('Arch', '架构'),
    BUG_FOUND: text('Bug', '缺陷'),
  }
  return labels[type] ?? type.replace(/_/g, ' ').toLowerCase()
}

function eventTypeColor(type: string) {
  if (type.includes('ERROR') || type.includes('BUG') || type.includes('LOSS'))
    return 'bg-red-500/10 text-red-600'
  if (type.includes('TEST'))
    return 'bg-green-500/10 text-green-700'
  if (type.includes('GIT'))
    return 'bg-blue-500/10 text-blue-700'
  if (type.includes('COMPLETION'))
    return 'bg-emerald-500/10 text-emerald-700'
  if (type.includes('BLOCKER') || type.includes('COMPACTION'))
    return 'bg-amber-500/10 text-amber-700'
  if (type.includes('DECISION') || type.includes('ARCHITECTURE'))
    return 'bg-purple-500/10 text-purple-700'
  return 'bg-muted/60 text-muted-foreground'
}

function ageText(value?: string) {
  if (!value)
    return '-'
  const ms = Date.now() - new Date(value).getTime()
  if (!Number.isFinite(ms))
    return value
  const minutes = Math.max(0, Math.floor(ms / 60000))
  if (minutes < 1)
    return text('just now', '刚刚')
  if (minutes < 60)
    return text(`${minutes}m ago`, `${minutes} 分钟前`)
  return text(`${Math.floor(minutes / 60)}h ago`, `${Math.floor(minutes / 60)} 小时前`)
}
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-6 p-6">
    <!-- Header -->
    <div class="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <div class="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
          AgentFactor
        </div>
        <h1 class="mt-1 text-3xl font-bold tracking-tight">
          {{ text('Platform Overview', '运行总览') }}
        </h1>
        <div class="mt-1 text-sm text-muted-foreground">
          {{ text('Real-time status of agents, tasks, and platform health', '实时反映智能体协作状态、任务进展与平台运行情况') }}
        </div>
      </div>
      <div class="flex flex-wrap gap-2">
        <FaButton variant="outline" :loading="loading" @click="loadAll()">
          <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
          {{ text('Refresh', '刷新') }}
        </FaButton>
        <FaButton @click="router.push('/tasks')">
          <FaIcon name="i-lucide:play" class="mr-2" />
          {{ text('New task', '启动任务') }}
        </FaButton>
      </div>
    </div>

    <!-- Stats row -->
    <div class="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
      <div class="rounded-xl border bg-background p-4">
        <div class="flex items-center gap-2 text-muted-foreground">
          <FaIcon name="i-lucide:layers" class="size-4" />
          <span class="text-xs">{{ text('Sessions', '协作会话') }}</span>
        </div>
        <div class="mt-3 text-3xl font-bold">
          {{ sessions.length }}
        </div>
      </div>

      <div class="rounded-xl border bg-background p-4">
        <div class="flex items-center gap-2 text-muted-foreground">
          <FaIcon name="i-lucide:terminal" class="size-4" />
          <span class="text-xs">{{ text('Terminals', '活跃终端') }}</span>
        </div>
        <div class="mt-3 text-3xl font-bold">
          {{ terminalMetrics.total }}
        </div>
        <div class="mt-1 text-xs text-muted-foreground">
          {{ terminalMetrics.error > 0 ? text(`${terminalMetrics.error} err`, `${terminalMetrics.error} 异常`) : text('all OK', '全部正常') }}
        </div>
      </div>

      <div class="rounded-xl border bg-background p-4" :class="terminalMetrics.running > 0 ? 'border-blue-400/40' : ''">
        <div class="flex items-center gap-2" :class="terminalMetrics.running > 0 ? 'text-blue-600' : 'text-muted-foreground'">
          <FaIcon name="i-lucide:activity" class="size-4" />
          <span class="text-xs">{{ text('Running', '运行中') }}</span>
        </div>
        <div class="mt-3 text-3xl font-bold" :class="terminalMetrics.running > 0 ? 'text-blue-600' : ''">
          {{ terminalMetrics.running }}
        </div>
      </div>

      <button
        class="rounded-xl border bg-background p-4 text-left transition hover:border-amber-400/60"
        :class="(state?.approvals_summary.pending ?? 0) > 0 ? 'border-amber-400/40' : ''"
        @click="router.push('/approvals')"
      >
        <div class="flex items-center gap-2" :class="(state?.approvals_summary.pending ?? 0) > 0 ? 'text-amber-600' : 'text-muted-foreground'">
          <FaIcon name="i-lucide:shield-alert" class="size-4" />
          <span class="text-xs">{{ text('Approvals', '待审批') }}</span>
        </div>
        <div class="mt-3 text-3xl font-bold" :class="(state?.approvals_summary.pending ?? 0) > 0 ? 'text-amber-600' : ''">
          {{ state?.approvals_summary.pending ?? 0 }}
        </div>
      </button>

      <button
        class="rounded-xl border bg-background p-4 text-left transition hover:border-purple-400/60"
        :class="(state?.pending_prompt_count ?? 0) > 0 ? 'border-purple-400/40' : ''"
        @click="router.push('/prompts')"
      >
        <div class="flex items-center gap-2" :class="(state?.pending_prompt_count ?? 0) > 0 ? 'text-purple-600' : 'text-muted-foreground'">
          <FaIcon name="i-lucide:message-square" class="size-4" />
          <span class="text-xs">{{ text('Prompts', '待回复') }}</span>
        </div>
        <div class="mt-3 text-3xl font-bold" :class="(state?.pending_prompt_count ?? 0) > 0 ? 'text-purple-600' : ''">
          {{ state?.pending_prompt_count ?? 0 }}
        </div>
      </button>

      <div class="rounded-xl border bg-background p-4" :class="completionRate ? 'border-emerald-400/40' : ''">
        <div class="flex items-center gap-2" :class="completionRate ? 'text-emerald-600' : 'text-muted-foreground'">
          <FaIcon name="i-lucide:check-circle-2" class="size-4" />
          <span class="text-xs">{{ text('Work done', '完成率') }}</span>
        </div>
        <div class="mt-3 text-3xl font-bold" :class="completionRate ? 'text-emerald-600' : ''">
          {{ completionRate ? `${completionRate.pct}%` : '-' }}
        </div>
        <div v-if="completionRate" class="mt-1 text-xs text-muted-foreground">
          {{ completionRate.done }} / {{ completionRate.total }}
        </div>
      </div>
    </div>

    <!-- Middle: Collaboration + Work progress -->
    <div class="grid gap-4 xl:grid-cols-[1.4fr_0.6fr]">
      <!-- Collaboration status -->
      <section class="rounded-xl border bg-background p-5">
        <div class="mb-4 flex items-center justify-between">
          <div>
            <h2 class="font-semibold">
              {{ text('Collaboration Status', '协作状态') }}
            </h2>
            <div class="mt-0.5 text-xs text-muted-foreground">
              {{ text('Active sessions and their terminals', '当前运行的会话与终端分布') }}
            </div>
          </div>
          <FaButton variant="outline" size="sm" @click="router.push('/sessions')">
            {{ text('Manage', '管理') }}
          </FaButton>
        </div>
        <div class="space-y-3">
          <div
            v-for="session in sessions"
            :key="session.name"
            class="rounded-lg border p-4 transition hover:border-primary/40"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex items-center gap-2">
                  <span class="font-medium">{{ session.name }}</span>
                  <span
                    v-if="session.terminals.filter(t => t.status === 'RUNNING').length > 0"
                    class="size-1.5 animate-pulse rounded-full bg-blue-500"
                  />
                </div>
                <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span>{{ session.terminals.length }} {{ text('terminals', '个终端') }}</span>
                  <span>{{ [...new Set(session.terminals.map(t => t.provider))].join(', ') }}</span>
                  <span
                    v-if="session.terminals.filter(t => t.status === 'RUNNING').length > 0"
                    class="text-blue-600"
                  >
                    {{ session.terminals.filter(t => t.status === 'RUNNING').length }} {{ text('running', '运行中') }}
                  </span>
                  <span
                    v-if="session.terminals.filter(t => t.status === 'ERROR').length > 0"
                    class="text-red-600"
                  >
                    {{ session.terminals.filter(t => t.status === 'ERROR').length }} {{ text('errors', '异常') }}
                  </span>
                </div>
              </div>
              <!-- Status dots -->
              <div class="flex shrink-0 flex-wrap gap-1">
                <div
                  v-for="terminal in session.terminals"
                  :key="terminal.id"
                  :title="`${terminal.window_name} · ${terminal.status}`"
                  class="size-2.5 rounded-full"
                  :class="terminalDot(terminal.status)"
                />
              </div>
            </div>
            <!-- Terminal badges -->
            <div class="mt-3 flex flex-wrap gap-1.5">
              <span
                v-for="terminal in session.terminals"
                :key="terminal.id"
                class="rounded-full border px-2 py-0.5 text-xs font-medium"
                :class="terminalBadge(terminal.status)"
              >
                {{ terminal.window_name.split('-').slice(0, 2).join('-') }}
              </span>
            </div>
          </div>
          <div
            v-if="sessions.length === 0"
            class="flex flex-col items-center py-10 text-muted-foreground"
          >
            <FaIcon name="i-lucide:bot" class="mb-3 size-10 opacity-30" />
            <div class="text-sm">
              {{ text('No active sessions.', '暂无活动会话。') }}
            </div>
            <FaButton class="mt-3" size="sm" variant="outline" @click="router.push('/tasks')">
              {{ text('Start a task', '启动任务') }}
            </FaButton>
          </div>
        </div>
      </section>

      <!-- Work progress -->
      <section class="rounded-xl border bg-background p-5">
        <div class="mb-4 flex items-center justify-between">
          <div>
            <h2 class="font-semibold">
              {{ text('Task Progress', '任务进展') }}
            </h2>
            <div class="mt-0.5 text-xs text-muted-foreground">
              {{ text('Work items in default project', '默认项目工作项统计') }}
            </div>
          </div>
          <FaButton variant="outline" size="sm" @click="router.push('/work-graph')">
            {{ text('Graph', '图谱') }}
          </FaButton>
        </div>
        <div v-if="workItems.length > 0" class="space-y-5">
          <!-- Overall progress -->
          <div>
            <div class="mb-2 flex items-center justify-between text-sm">
              <span class="text-muted-foreground">{{ text('Completion', '整体完成度') }}</span>
              <span class="font-semibold">{{ completionRate?.pct ?? 0 }}%</span>
            </div>
            <div class="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                class="h-full bg-emerald-500 transition-all duration-500"
                :style="{ width: `${completionRate?.pct ?? 0}%` }"
              />
            </div>
          </div>
          <!-- Status breakdown -->
          <div class="space-y-3">
            <div v-for="group in workStatusGroups" :key="group.key" class="space-y-1">
              <div class="flex items-center justify-between text-xs">
                <div class="flex items-center gap-2">
                  <div class="size-2 shrink-0 rounded-full" :class="group.color" />
                  <span class="text-muted-foreground">{{ group.label }}</span>
                </div>
                <span class="font-medium">{{ group.count }}</span>
              </div>
              <div class="h-1 w-full overflow-hidden rounded-full bg-muted">
                <div
                  class="h-full transition-all duration-500"
                  :class="group.color"
                  :style="{ width: workItems.length ? `${Math.round((group.count / workItems.length) * 100)}%` : '0%' }"
                />
              </div>
            </div>
          </div>
        </div>
        <div v-else class="flex flex-col items-center py-10 text-muted-foreground">
          <FaIcon name="i-lucide:git-branch" class="mb-3 size-10 opacity-30" />
          <div class="text-sm">
            {{ text('No work items yet.', '暂无工作项。') }}
          </div>
          <FaButton class="mt-3" size="sm" variant="outline" @click="router.push('/work-graph')">
            {{ text('Create items', '创建工作项') }}
          </FaButton>
        </div>
      </section>
    </div>

    <!-- Bottom: Platform health + Recent activity -->
    <div class="grid gap-4 xl:grid-cols-[0.65fr_1.35fr]">
      <!-- Platform health -->
      <section class="rounded-xl border bg-background p-5">
        <div class="mb-4 flex items-center justify-between">
          <div>
            <h2 class="font-semibold">
              {{ text('Platform Health', '平台状态') }}
            </h2>
            <div class="mt-0.5 text-xs text-muted-foreground">
              {{ text('Provider availability', '执行方可用性') }}
            </div>
          </div>
          <FaButton variant="outline" size="sm" @click="router.push('/providers')">
            {{ text('Details', '详情') }}
          </FaButton>
        </div>
        <div class="space-y-2">
          <div
            v-for="provider in state?.providers ?? []"
            :key="provider.key"
            class="flex items-center justify-between rounded-lg border p-3"
          >
            <div class="min-w-0">
              <div class="text-sm font-medium">
                {{ provider.label }}
              </div>
              <div class="truncate text-xs text-muted-foreground">
                {{ provider.binary }}
              </div>
            </div>
            <div class="flex items-center gap-1.5 text-xs">
              <div
                class="size-2 rounded-full"
                :class="provider.available ? 'bg-green-500' : 'bg-red-500'"
              />
              <span :class="provider.available ? 'text-green-600' : 'text-red-500'">
                {{ provider.available ? text('OK', '正常') : text('Missing', '未配置') }}
              </span>
            </div>
          </div>
        </div>
        <div
          v-if="terminalMetrics.error > 0"
          class="mt-3 flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-600"
        >
          <FaIcon name="i-lucide:alert-triangle" class="shrink-0" />
          {{ text(`${terminalMetrics.error} terminal(s) in error.`, `${terminalMetrics.error} 个终端处于异常状态。`) }}
        </div>
        <div
          v-else-if="terminalMetrics.total > 0"
          class="mt-3 flex items-center gap-2 rounded-lg border border-green-500/30 bg-green-500/5 p-3 text-xs text-green-700"
        >
          <FaIcon name="i-lucide:check-circle-2" class="shrink-0" />
          {{ text('All terminals healthy.', '所有终端运行正常。') }}
        </div>
      </section>

      <!-- Recent activity -->
      <section class="rounded-xl border bg-background p-5">
        <div class="mb-4 flex items-center justify-between">
          <div>
            <h2 class="font-semibold">
              {{ text('Recent Activity', '近期动态') }}
            </h2>
            <div class="mt-0.5 text-xs text-muted-foreground">
              {{ text('Latest signals from the event log', '来自事件流的最新信号') }}
            </div>
          </div>
          <FaButton variant="outline" size="sm" @click="router.push('/context')">
            {{ text('Full feed', '完整事件流') }}
          </FaButton>
        </div>
        <div class="space-y-1">
          <div
            v-for="event in events"
            :key="event.id"
            class="flex items-center gap-3 rounded-md px-2 py-2 text-sm transition hover:bg-muted/40"
          >
            <span
              class="shrink-0 rounded px-1.5 py-0.5 text-xs font-medium"
              :class="eventTypeColor(event.type)"
            >
              {{ eventTypeLabel(event.type) }}
            </span>
            <span class="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              {{ event.terminal_id ? `${event.terminal_id.slice(0, 14)}…` : 'system' }}
            </span>
            <span class="shrink-0 text-xs text-muted-foreground">
              {{ ageText(event.timestamp) }}
            </span>
          </div>
          <div
            v-if="events.length === 0"
            class="flex flex-col items-center py-8 text-muted-foreground"
          >
            <FaIcon name="i-lucide:zap" class="mb-2 size-8 opacity-30" />
            <div class="text-sm">
              {{ text('No events recorded yet.', '暂无事件记录。') }}
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
