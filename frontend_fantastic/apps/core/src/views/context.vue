<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { ContextPack, DashboardState, EventLogItem, Snapshot, SnapshotDiff, Terminal, TerminalMetrics } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'ConductorContext',
})

const loading = ref(false)
const actionLoading = ref(false)
const state = ref<DashboardState | null>(null)
const events = ref<EventLogItem[]>([])
const metrics = ref<TerminalMetrics | null>(null)
const latestPack = ref<ContextPack | null>(null)
const latestSnap = ref<Snapshot | null>(null)
const snapshots = ref<Snapshot[]>([])
const diff = ref<SnapshotDiff | null>(null)
const selectedTerminalId = ref('')
const eventType = ref('')
const packQuery = ref('current task status, decisions, blockers, files changed')
const tokenBudget = ref(8000)
const basePackId = ref('')
const snapshotA = ref('')
const snapshotB = ref('')
const route = useRoute()
const router = useRouter()
const { text } = useLanguage()
let refreshTimer: number | undefined

const terminals = computed<Terminal[]>(() => {
  return (state.value?.sessions ?? []).flatMap(session => session.terminals)
})

const selectedTerminal = computed(() => terminals.value.find(item => item.id === selectedTerminalId.value))

const snapshotOptions = computed(() => snapshots.value.map(item => ({
  id: snapshotId(item),
  label: `${snapshotId(item)} / cursor ${item.event_cursor}`,
})))

onMounted(async () => {
  selectedTerminalId.value = typeof route.query.terminal === 'string' ? route.query.terminal : ''
  await load()
  refreshTimer = window.setInterval(() => {
    void loadEventsOnly(true).catch(() => {})
  }, 5000)
})

onBeforeUnmount(() => {
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
  }
})

async function load(silent = false) {
  loading.value = !silent
  try {
    state.value = await apiConductor.dashboardState(silent)
    if (!selectedTerminalId.value && terminals.value.length) {
      selectedTerminalId.value = terminals.value[0].id
    }
    await Promise.all([loadEventsOnly(), loadTerminalContext()])
  }
  finally {
    loading.value = false
  }
}

async function loadEventsOnly(silent = false) {
  events.value = await apiConductor.events({
    terminal_id: selectedTerminalId.value || undefined,
    event_type: eventType.value || undefined,
    limit: 120,
  }, silent)
}

async function loadTerminalContext() {
  if (!selectedTerminalId.value) {
    metrics.value = null
    latestPack.value = null
    latestSnap.value = null
    snapshots.value = []
    return
  }
  const [metricsResult, packResult, snapResult, historyResult] = await Promise.allSettled([
    apiConductor.terminalMetrics(selectedTerminalId.value, true),
    apiConductor.latestContextPack(selectedTerminalId.value, true),
    apiConductor.latestSnapshot(selectedTerminalId.value, true),
    apiConductor.compactionHistory(selectedTerminalId.value),
  ])
  metrics.value = metricsResult.status === 'fulfilled' ? metricsResult.value : null
  latestPack.value = packResult.status === 'fulfilled' ? packResult.value : null
  latestSnap.value = snapResult.status === 'fulfilled' ? snapResult.value : null
  snapshots.value = historyResult.status === 'fulfilled' ? historyResult.value : []
  if (!basePackId.value) {
    basePackId.value = latestPack.value?.pack_id ?? ''
  }
}

async function selectTerminal(id: string) {
  selectedTerminalId.value = id
  router.replace({ path: '/context', query: id ? { terminal: id } : {} })
  await Promise.all([loadEventsOnly(), loadTerminalContext()])
}

async function buildPack(differential = false) {
  if (!selectedTerminalId.value) {
    return
  }
  actionLoading.value = true
  try {
    latestPack.value = differential && basePackId.value
      ? await apiConductor.differentialContextPack(selectedTerminalId.value, {
          base_pack_id: basePackId.value,
          query: packQuery.value,
        })
      : await apiConductor.buildContextPack(selectedTerminalId.value, {
          query: packQuery.value,
          token_budget: tokenBudget.value,
        })
    basePackId.value = latestPack.value.pack_id
    faToast.success(differential ? text('Differential pack generated.', '差异上下文包已生成。') : text('Context pack generated.', '上下文包已生成。'))
    await loadEventsOnly()
  }
  finally {
    actionLoading.value = false
  }
}

async function triggerCompaction() {
  if (!selectedTerminalId.value) {
    return
  }
  actionLoading.value = true
  try {
    const snap = await apiConductor.triggerCompaction(selectedTerminalId.value)
    latestSnap.value = snap
    faToast.success(text('Compaction triggered.', '压缩已触发。'))
    await loadTerminalContext()
  }
  finally {
    actionLoading.value = false
  }
}

async function loadSnapshotDiff() {
  if (!snapshotA.value || !snapshotB.value) {
    faToast.warning(text('Select two snapshots first.', '请先选择两个快照。'))
    return
  }
  diff.value = await apiConductor.compactionDiff(snapshotA.value, snapshotB.value)
}

function snapshotId(item: Partial<Snapshot>) {
  return item.id || item.snapshot_id || ''
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

function pretty(value: unknown) {
  return JSON.stringify(value, null, 2)
}
</script>

<template>
  <div class="mx-auto p-6 max-w-7xl space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <div class="text-sm text-muted-foreground">{{ text('Event feed, context packs, compaction health', '事件流、上下文快照与压缩状态监控') }}</div>
        <h1 class="mt-1 text-2xl font-semibold">{{ text('Overseer Console', '运行监控台') }}</h1>
      </div>
      <FaButton variant="outline" :loading="loading" @click="load">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <div class="grid gap-4 lg:grid-cols-[280px_1fr]">
      <section class="border rounded-lg bg-background p-4">
        <div class="mb-3 font-semibold">{{ text('Terminals', '终端') }}</div>
        <div class="space-y-2">
          <button
            v-for="terminal in terminals"
            :key="terminal.id"
            class="w-full rounded-md border p-3 text-left text-sm transition hover:border-primary"
            :class="selectedTerminalId === terminal.id ? 'border-primary bg-primary/5' : ''"
            @click="selectTerminal(terminal.id)"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="truncate font-medium">{{ terminal.window_name }}</span>
              <span class="rounded bg-muted px-2 py-1 text-xs">{{ terminal.status }}</span>
            </div>
            <div class="mt-1 truncate text-xs text-muted-foreground">{{ terminal.session_name }} / {{ terminal.id }}</div>
          </button>
          <div v-if="terminals.length === 0" class="py-6 text-center text-sm text-muted-foreground">No terminals.</div>
        </div>
      </section>

      <section class="space-y-4">
        <div class="grid gap-4 md:grid-cols-4">
          <div class="rounded-lg border bg-background p-4">
          <div class="text-xs text-muted-foreground">{{ text('Velocity', '输出速度') }}</div>
            <div class="mt-2 text-2xl font-semibold">{{ metrics?.output_velocity_tpm?.toFixed(1) ?? '-' }}</div>
            <div class="text-xs text-muted-foreground">tokens/min</div>
          </div>
          <div class="rounded-lg border bg-background p-4">
          <div class="text-xs text-muted-foreground">{{ text('Error Density', '错误密度') }}</div>
            <div class="mt-2 text-2xl font-semibold">{{ metrics ? metrics.error_density.toFixed(2) : '-' }}</div>
            <div class="text-xs text-muted-foreground">rolling sample</div>
          </div>
          <div class="rounded-lg border bg-background p-4">
          <div class="text-xs text-muted-foreground">{{ text('Pack Age', '上下文包时效') }}</div>
            <div class="mt-2 text-2xl font-semibold">{{ ageText(latestPack?.created_at) }}</div>
            <div class="truncate text-xs text-muted-foreground">{{ latestPack?.pack_id || 'no pack' }}</div>
          </div>
          <div class="rounded-lg border bg-background p-4">
          <div class="text-xs text-muted-foreground">{{ text('Snapshot Cursor', '快照进度') }}</div>
            <div class="mt-2 text-2xl font-semibold">{{ latestSnap?.event_cursor ?? '-' }}</div>
            <div class="truncate text-xs text-muted-foreground">{{ snapshotId(latestSnap || {}) || 'no snapshot' }}</div>
          </div>
        </div>

        <div class="grid gap-4 xl:grid-cols-2">
          <section class="border rounded-lg bg-background p-4">
            <div class="mb-3 flex items-center justify-between">
              <div>
                <h2 class="text-lg font-semibold">{{ text('Context Pack', '上下文包') }}</h2>
                <div class="text-xs text-muted-foreground">{{ selectedTerminal?.id || 'Select a terminal' }}</div>
              </div>
              <FaButton size="sm" variant="outline" @click="router.push({ path: '/sessions', query: { terminal: selectedTerminalId } })">
                {{ text('Console', '控制台') }}
              </FaButton>
            </div>
            <div class="space-y-3">
              <FaInput v-model="packQuery" class="w-full" placeholder="Semantic retrieval query" />
              <div class="grid min-w-0 gap-3 lg:grid-cols-[180px_minmax(0,1fr)]">
                <FaInput v-model.number="tokenBudget" class="min-w-0" type="number" placeholder="Token budget" />
                <FaInput v-model="basePackId" class="min-w-0" placeholder="Base pack ID" />
              </div>
              <div class="flex flex-wrap gap-2">
                <FaButton :disabled="!selectedTerminalId" :loading="actionLoading" @click="buildPack(false)">{{ text('Generate pack', '生成上下文包') }}</FaButton>
                <FaButton variant="outline" :disabled="!selectedTerminalId || !basePackId" :loading="actionLoading" @click="buildPack(true)">{{ text('Generate diff', '生成差异包') }}</FaButton>
                <FaButton variant="outline" :disabled="!selectedTerminalId" :loading="actionLoading" @click="loadTerminalContext">{{ text('Latest', '最新') }}</FaButton>
              </div>
              <pre class="max-h-96 overflow-auto rounded-md bg-muted/40 p-4 text-xs">{{ latestPack ? pretty(latestPack) : 'No context pack.' }}</pre>
            </div>
          </section>

          <section class="border rounded-lg bg-background p-4">
            <div class="mb-3 flex items-center justify-between">
              <div>
                <h2 class="text-lg font-semibold">{{ text('Compaction', '上下文压缩') }}</h2>
                <div class="text-xs text-muted-foreground">{{ snapshots.length }} snapshots</div>
              </div>
              <FaButton size="sm" :disabled="!selectedTerminalId" :loading="actionLoading" @click="triggerCompaction">{{ text('Trigger', '立即压缩') }}</FaButton>
            </div>
            <div class="grid gap-3 md:grid-cols-2">
              <select v-model="snapshotA" class="border rounded-md bg-background px-3 py-2 text-sm">
                <option value="">Snapshot A</option>
                <option v-for="item in snapshotOptions" :key="item.id" :value="item.id">{{ item.label }}</option>
              </select>
              <select v-model="snapshotB" class="border rounded-md bg-background px-3 py-2 text-sm">
                <option value="">Snapshot B</option>
                <option v-for="item in snapshotOptions" :key="item.id" :value="item.id">{{ item.label }}</option>
              </select>
            </div>
            <div class="mt-3 flex gap-2">
              <FaButton variant="outline" size="sm" @click="loadSnapshotDiff">{{ text('Diff snapshots', '对比快照') }}</FaButton>
              <FaButton variant="outline" size="sm" :disabled="!selectedTerminalId" @click="loadTerminalContext">{{ text('Reload history', '重载历史') }}</FaButton>
            </div>
            <div class="mt-4 space-y-2">
              <div v-for="snap in snapshots" :key="snapshotId(snap)" class="rounded-md border p-3 text-sm">
                <div class="flex items-center justify-between gap-3">
                  <span class="font-medium">{{ snapshotId(snap) }}</span>
                  <span class="text-xs text-muted-foreground">cursor {{ snap.event_cursor }}</span>
                </div>
                <div class="mt-1 line-clamp-2 text-xs text-muted-foreground">{{ snap.summary_text }}</div>
              </div>
            </div>
            <pre v-if="diff" class="mt-4 max-h-80 overflow-auto rounded-md bg-muted/40 p-4 text-xs">{{ pretty(diff) }}</pre>
          </section>
        </div>

        <section class="border rounded-lg bg-background p-4">
          <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="text-lg font-semibold">{{ text('Event Feed', '事件流') }}</h2>
              <div class="text-xs text-muted-foreground">{{ text('Newest first, filtered by terminal and type', '最新优先，可按终端和类型筛选') }}</div>
            </div>
            <div class="flex gap-2">
              <FaInput v-model="eventType" placeholder="Event type filter" @keydown.enter.prevent="loadEventsOnly" />
              <FaButton variant="outline" @click="loadEventsOnly">{{ text('Apply', '应用') }}</FaButton>
            </div>
          </div>
          <div class="space-y-2">
            <article v-for="event in events" :key="event.id" class="rounded-md border p-3">
              <div class="flex flex-wrap items-center justify-between gap-3 text-sm">
                <div class="font-medium">#{{ event.id }} {{ event.type }}</div>
                <div class="text-xs text-muted-foreground">{{ event.timestamp }}</div>
              </div>
              <div class="mt-1 text-xs text-muted-foreground">{{ event.terminal_id || 'system' }} / source {{ event.source_id || '-' }}</div>
              <pre class="mt-2 max-h-44 overflow-auto rounded bg-muted/40 p-3 text-xs">{{ pretty(event.payload) }}</pre>
            </article>
            <div v-if="events.length === 0" class="py-8 text-center text-muted-foreground">{{ text('No events.', '暂无事件。') }}</div>
          </div>
        </section>
      </section>
    </div>
  </div>
</template>
