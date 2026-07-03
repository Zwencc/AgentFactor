<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { ContextPack, DashboardState, Snapshot, Terminal, TerminalAlert, TerminalMetrics, WorkItem } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'
import { useConductorProjectStore } from '@/store/modules/conductor/project'

defineOptions({
  name: 'ConductorSessions',
})

const loading = ref(false)
const outputLoading = ref(false)
const autoRefreshOutput = ref(true)
const requiresApproval = ref(false)
const state = ref<DashboardState | null>(null)
const selected = ref<Terminal | null>(null)
const output = ref('')
const message = ref('')
const supervisorId = ref('')
const approvalMetadata = ref('')
const route = useRoute()
const router = useRouter()
const { text } = useLanguage()
const metricsByTerminal = reactive<Record<string, TerminalMetrics | null>>({})
const packByTerminal = reactive<Record<string, ContextPack | null>>({})
const snapshotByTerminal = reactive<Record<string, Snapshot | null>>({})
const contextActionLoading = reactive<Record<string, boolean>>({})
const projectStore = useConductorProjectStore()
const workItemsByTerminal = reactive<Record<string, WorkItem[]>>({})
let refreshTimer: number | undefined

const terminalAlerts = computed(() => state.value?.terminal_alerts ?? [])

const supervisors = computed(() => {
  if (!selected.value) {
    return []
  }
  return (state.value?.sessions ?? [])
    .flatMap(session => session.terminals)
    .filter(terminal =>
      terminal.session_name === selected.value?.session_name
      && terminal.window_name.startsWith('supervisor-'),
    )
})

onMounted(async () => {
  await loadState()
  const terminalId = typeof route.query.terminal === 'string' ? route.query.terminal : ''
  if (terminalId) {
    const terminal = findTerminal(terminalId)
    if (terminal) {
      await openTerminal(terminal)
    }
  }
  refreshTimer = window.setInterval(() => {
    void (async () => {
      await loadState(true)
      if (selected.value && autoRefreshOutput.value) {
        await refreshOutput(false, true)
      }
    })().catch(() => {})
  }, 5000)
})

onBeforeUnmount(() => {
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
  }
})

watch(() => projectStore.currentProject, () => {
  void loadWorkItemAttribution()
})

async function loadState(silent = false) {
  loading.value = !silent
  try {
    state.value = await apiConductor.dashboardState(silent)
    await Promise.all([loadTerminalContextSummaries(), loadWorkItemAttribution()])
    if (selected.value) {
      const current = state.value.sessions
        .flatMap(session => session.terminals)
        .find(terminal => terminal.id === selected.value?.id)
      selected.value = current ?? null
      if (!selected.value) {
        output.value = ''
      }
    }
  }
  finally {
    loading.value = false
  }
}

async function loadWorkItemAttribution() {
  try {
    const items = await apiConductor.workItems(projectStore.currentProject)
    const map: Record<string, WorkItem[]> = {}
    for (const item of items) {
      if (item.owner_terminal_id) {
        ;(map[item.owner_terminal_id] ??= []).push(item)
      }
    }
    Object.assign(workItemsByTerminal, map)
  }
  catch {}
}

async function loadTerminalContextSummaries() {
  const terminals = (state.value?.sessions ?? []).flatMap(session => session.terminals)
  await Promise.all(terminals.map(async (terminal) => {
    const [metricsResult, packResult, snapshotResult] = await Promise.allSettled([
      apiConductor.terminalMetrics(terminal.id, true),
      apiConductor.latestContextPack(terminal.id, true),
      apiConductor.latestSnapshot(terminal.id, true),
    ])
    metricsByTerminal[terminal.id] = metricsResult.status === 'fulfilled' ? metricsResult.value : null
    packByTerminal[terminal.id] = packResult.status === 'fulfilled' ? packResult.value : null
    snapshotByTerminal[terminal.id] = snapshotResult.status === 'fulfilled' ? snapshotResult.value : null
  }))
}

function findTerminal(terminalId: string) {
  return (state.value?.sessions ?? [])
    .flatMap(session => session.terminals)
    .find(terminal => terminal.id === terminalId)
}

function terminalAlert(terminalId: string) {
  return terminalAlerts.value.find(alert => alert.terminal_id === terminalId)
}

function alertTone(alert: TerminalAlert) {
  return alert.severity === 'error'
    ? 'border-red-300 bg-red-50 text-red-900'
    : 'border-amber-300 bg-amber-50 text-amber-900'
}

function alertToneByTerminal(terminalId: string) {
  const alert = terminalAlert(terminalId)
  return alert ? alertTone(alert) : ''
}

async function openAlertTerminal(alert: TerminalAlert) {
  const terminal = findTerminal(alert.terminal_id)
  if (terminal) {
    await openTerminal(terminal)
  }
}

async function closeAlertTerminal(alert: TerminalAlert) {
  const terminal = findTerminal(alert.terminal_id)
  if (terminal) {
    await closeTerminal(terminal)
  }
}

async function openTerminal(terminal: Terminal) {
  selected.value = terminal
  supervisorId.value = supervisors.value[0]?.id ?? ''
  await refreshOutput()
}

async function refreshOutput(showLoading = true, silent = false) {
  if (!selected.value) {
    output.value = ''
    return
  }
  outputLoading.value = showLoading
  try {
    const res = await apiConductor.terminalOutput(selected.value.id, silent)
    output.value = res.output || '(no output captured yet)'
  }
  finally {
    outputLoading.value = false
  }
}

async function sendMessage() {
  if (!selected.value || !message.value.trim()) {
    return
  }
  if (requiresApproval.value && !supervisorId.value) {
    faToast.warning(text('Select a supervisor for approval.', '请选择审批主管。'))
    return
  }
  await apiConductor.sendInput(selected.value.id, {
    message: message.value,
    requires_approval: requiresApproval.value,
    supervisor_id: requiresApproval.value ? supervisorId.value : undefined,
    metadata_payload: requiresApproval.value ? approvalMetadata.value.trim() || null : undefined,
  })
  message.value = ''
  faToast.success(requiresApproval.value ? text('Approval request queued.', '审批请求已加入队列。') : text('Message sent.', '消息已发送。'))
  await refreshOutput()
}

async function closeTerminal(terminal: Terminal) {
  await apiConductor.deleteTerminal(terminal.id)
  faToast.success(text('Terminal closed.', '终端已关闭。'))
  selected.value = null
  output.value = ''
  await loadState()
}

async function closeSession(sessionName: string) {
  await apiConductor.deleteSession(sessionName)
  faToast.success(text('Session closed.', '会话已关闭。'))
  await loadState()
}

async function copyTerminalId() {
  if (!selected.value) {
    return
  }
  await navigator.clipboard?.writeText(selected.value.id)
  faToast.success(text('Terminal ID copied.', '终端 ID 已复制。'))
}

async function generateContextPack(terminal: Terminal) {
  contextActionLoading[terminal.id] = true
  try {
    packByTerminal[terminal.id] = await apiConductor.buildContextPack(terminal.id, {
      query: `${terminal.session_name} ${terminal.window_name} latest task context decisions blockers`,
      token_budget: 8000,
    })
    faToast.success(text('Context pack generated.', '上下文包已生成。'))
  }
  finally {
    contextActionLoading[terminal.id] = false
  }
}

async function triggerTerminalCompaction(terminal: Terminal) {
  contextActionLoading[terminal.id] = true
  try {
    snapshotByTerminal[terminal.id] = await apiConductor.triggerCompaction(terminal.id)
    faToast.success(text('Compaction triggered.', '压缩已触发。'))
  }
  finally {
    contextActionLoading[terminal.id] = false
  }
}

async function viewLatestSnapshot(terminal: Terminal) {
  const latest = await apiConductor.latestSnapshot(terminal.id)
  snapshotByTerminal[terminal.id] = latest
  router.push({ path: '/context', query: { terminal: terminal.id } })
}

function packAge(terminalId: string) {
  return ageText(packByTerminal[terminalId]?.created_at)
}

function snapshotLag(terminalId: string) {
  const cursor = snapshotByTerminal[terminalId]?.event_cursor
  return cursor === undefined ? '-' : `cursor ${cursor}`
}

function ageText(value?: string) {
  if (!value) {
    return '-'
  }
  const ms = Date.now() - new Date(value).getTime()
  if (!Number.isFinite(ms)) {
    return value
  }
  const minutes = Math.max(0, Math.floor(ms / 60000))
  if (minutes < 60) {
    return `${minutes}m`
  }
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}
</script>

<template>
  <div class="mx-auto p-6 max-w-7xl space-y-6">
    <div class="flex items-end justify-between">
      <div>
        <div class="text-sm text-muted-foreground">{{ text('Operate active tmux workspaces', '查看和操作当前运行的终端') }}</div>
        <h1 class="mt-1 text-2xl font-semibold">{{ text('Sessions', '终端会话') }}</h1>
      </div>
      <FaButton variant="outline" :loading="loading" @click="loadState">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <div v-if="terminalAlerts.length" class="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
      <div class="mb-3 flex items-center gap-2 font-semibold">
        <FaIcon name="i-lucide:triangle-alert" class="size-4" />
        {{ text('Attention required', '需要处理') }}
      </div>
      <div class="space-y-2">
        <div
          v-for="alert in terminalAlerts"
          :key="alert.id"
          class="flex flex-col gap-3 rounded-md border px-3 py-2 md:flex-row md:items-center md:justify-between"
          :class="alertTone(alert)"
        >
          <div>
            <div class="font-medium">{{ alert.window_name }} / {{ alert.terminal_id }}</div>
            <div class="mt-1 text-xs opacity-80">{{ alert.message }}</div>
          </div>
          <div class="flex shrink-0 gap-2">
            <FaButton variant="outline" size="sm" @click="openAlertTerminal(alert)">
              <FaIcon name="i-lucide:terminal" class="mr-2" />
              {{ text('Inspect', '查看') }}
            </FaButton>
            <FaButton variant="outline" size="sm" @click="closeAlertTerminal(alert)">
              <FaIcon name="i-lucide:x" class="mr-2" />
              {{ text('Close', '关闭') }}
            </FaButton>
          </div>
        </div>
      </div>
    </div>

    <div class="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
      <section class="space-y-4">
        <div v-for="session in state?.sessions ?? []" :key="session.name" class="border rounded-lg bg-background p-4">
          <div class="mb-3 flex items-center justify-between gap-3">
            <div>
              <div class="font-semibold">{{ session.name }}</div>
              <div class="text-xs text-muted-foreground">{{ session.terminals.length }} terminals</div>
            </div>
            <FaButton variant="outline" size="sm" @click="closeSession(session.name)">{{ text('Close session', '关闭会话') }}</FaButton>
          </div>
          <div class="space-y-2">
            <button
              v-for="terminal in session.terminals"
              :key="terminal.id"
              class="w-full rounded-md border p-3 text-left transition hover:border-primary"
              :class="selected?.id === terminal.id ? 'border-primary bg-primary/5' : ''"
              @click="openTerminal(terminal)"
            >
              <div class="flex items-center justify-between gap-3">
                <div class="font-medium">{{ terminal.window_name }}</div>
                <div class="flex items-center gap-2">
                  <span
                    v-if="terminalAlert(terminal.id)"
                    class="inline-flex items-center gap-1 rounded border px-2 py-1 text-xs"
                    :class="alertToneByTerminal(terminal.id)"
                  >
                    <FaIcon name="i-lucide:triangle-alert" class="size-3" />
                    {{ terminalAlert(terminal.id)?.kind }}
                  </span>
                  <span class="rounded bg-muted px-2 py-1 text-xs">{{ terminal.status }}</span>
                </div>
              </div>
              <div class="mt-1 text-xs text-muted-foreground">
                {{ terminal.provider }} / {{ terminal.agent_profile || 'default' }} / {{ terminal.id }}
              </div>
              <div v-if="workItemsByTerminal[terminal.id]?.length" class="mt-2 flex flex-wrap gap-1">
                <span
                  v-for="wi in workItemsByTerminal[terminal.id]"
                  :key="wi.id"
                  class="inline-flex items-center gap-1 rounded border bg-primary/5 px-1.5 py-0.5 text-xs text-primary"
                >
                  <FaIcon name="i-lucide:square-check" class="size-3" />
                  {{ wi.title }}
                </span>
              </div>
              <div class="mt-3 grid gap-2 text-xs text-muted-foreground md:grid-cols-3">
                <div class="rounded border bg-muted/20 p-2">
                  <div>{{ text('Velocity', '输出速度') }}</div>
                  <div class="mt-1 font-medium text-foreground">{{ metricsByTerminal[terminal.id]?.output_velocity_tpm?.toFixed(1) ?? '-' }}</div>
                </div>
                <div class="rounded border bg-muted/20 p-2">
                  <div>{{ text('Pack age', '上下文包时间') }}</div>
                  <div class="mt-1 font-medium text-foreground">{{ packAge(terminal.id) }}</div>
                </div>
                <div class="rounded border bg-muted/20 p-2">
                  <div>{{ text('Snapshot', '快照') }}</div>
                  <div class="mt-1 font-medium text-foreground">{{ snapshotLag(terminal.id) }}</div>
                </div>
              </div>
            </button>
          </div>
        </div>
        <div v-if="!state?.sessions.length" class="border rounded-lg bg-background p-8 text-center text-muted-foreground">
          {{ text('No active sessions.', '暂无活动会话。') }}
        </div>
      </section>

      <section class="border rounded-lg bg-background p-4">
        <div class="mb-4 flex items-center justify-between">
          <div>
            <h2 class="text-lg font-semibold">{{ text('Terminal Console', '终端控制台') }}</h2>
            <div class="text-xs text-muted-foreground">{{ selected?.id || text('Select a terminal', '请选择终端') }}</div>
          </div>
          <div v-if="selected" class="flex gap-2">
            <FaButton variant="outline" size="sm" :loading="outputLoading" @click="refreshOutput()">
              <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
              {{ text('Output', '输出') }}
            </FaButton>
            <FaButton variant="outline" size="sm" :loading="contextActionLoading[selected.id]" @click="generateContextPack(selected)">
              {{ text('Pack', '上下文包') }}
            </FaButton>
            <FaButton variant="outline" size="sm" :loading="contextActionLoading[selected.id]" @click="triggerTerminalCompaction(selected)">
              {{ text('Compact', '压缩') }}
            </FaButton>
            <FaButton variant="outline" size="sm" @click="viewLatestSnapshot(selected)">
              {{ text('Snapshot', '快照') }}
            </FaButton>
            <FaButton variant="outline" size="sm" @click="copyTerminalId">{{ text('Copy ID', '复制 ID') }}</FaButton>
            <FaButton variant="outline" size="sm" @click="closeTerminal(selected)">{{ text('Close', '关闭') }}</FaButton>
          </div>
        </div>

        <div v-if="selected" class="mb-4 grid gap-3 rounded-md border p-3 text-sm md:grid-cols-4">
          <div>
            <div class="text-xs text-muted-foreground">{{ text('Session', '会话') }}</div>
            <div class="truncate">{{ selected.session_name }}</div>
          </div>
          <div>
            <div class="text-xs text-muted-foreground">{{ text('Window', '窗口') }}</div>
            <div class="truncate">{{ selected.window_name }}</div>
          </div>
          <div>
            <div class="text-xs text-muted-foreground">{{ text('Provider', '运行方') }}</div>
            <div>{{ selected.provider }}</div>
          </div>
          <div>
            <div class="text-xs text-muted-foreground">{{ text('Status', '状态') }}</div>
            <div>{{ selected.status }}</div>
          </div>
          <div>
            <div class="text-xs text-muted-foreground">{{ text('Error density', '错误密度') }}</div>
            <div>{{ metricsByTerminal[selected.id]?.error_density?.toFixed(2) ?? '-' }}</div>
          </div>
          <div>
            <div class="text-xs text-muted-foreground">{{ text('Idle streak', '空闲时长') }}</div>
            <div>{{ metricsByTerminal[selected.id]?.idle_streak_minutes?.toFixed(1) ?? '-' }} min</div>
          </div>
          <div>
            <div class="text-xs text-muted-foreground">{{ text('Context pack age', '上下文包时间') }}</div>
            <div>{{ packAge(selected.id) }}</div>
          </div>
          <div>
            <div class="text-xs text-muted-foreground">{{ text('Snapshot lag', '快照游标') }}</div>
            <div>{{ snapshotLag(selected.id) }}</div>
          </div>
        </div>

        <pre class="min-h-96 max-h-[560px] overflow-auto rounded-md bg-muted/40 p-4 text-xs">{{ output || text('No terminal selected.', '未选择终端。') }}</pre>
        <div class="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
          <input id="auto-refresh-output" v-model="autoRefreshOutput" type="checkbox" class="size-4">
          <label for="auto-refresh-output">{{ text('Auto refresh output every 5 seconds', '每 5 秒自动刷新输出') }}</label>
        </div>

        <div class="mt-4 space-y-3">
          <div class="flex gap-3">
            <FaInput v-model="message" class="flex-1" :placeholder="text('Message to selected terminal', '发送到所选终端的消息')" @keydown.enter.prevent="sendMessage" />
            <FaButton :disabled="!selected" @click="sendMessage">{{ text('Send', '发送') }}</FaButton>
          </div>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="requiresApproval" type="checkbox" class="size-4">
            {{ text('Require supervisor approval', '需要主管审批') }}
          </label>
          <div v-if="requiresApproval" class="grid gap-3 md:grid-cols-2">
            <select v-model="supervisorId" class="border rounded-md bg-background px-3 py-2">
              <option value="">{{ text('Select supervisor', '选择主管') }}</option>
              <option v-for="terminal in supervisors" :key="terminal.id" :value="terminal.id">
                {{ terminal.window_name }} / {{ terminal.id }}
              </option>
            </select>
            <FaInput v-model="approvalMetadata" :placeholder="text('Approval metadata or reason', '审批元数据或原因')" />
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
