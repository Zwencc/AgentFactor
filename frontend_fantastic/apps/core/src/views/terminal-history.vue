<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { ConversationTurn, DashboardState, Terminal, TerminalAnalysis, TerminalReview, VerifierRun } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'TerminalHistory',
})

const loading = ref(false)
const analysisLoading = reactive<Record<string, boolean>>({})
const analyses = ref<TerminalAnalysis[]>([])
const selected = ref<TerminalAnalysis | null>(null)
const state = ref<DashboardState | null>(null)
const { text } = useLanguage()

// Tabs: analysis | review | conversation | raw-log
type TabId = 'analysis' | 'review' | 'conversation' | 'raw-log'
const activeTab = ref<TabId>('analysis')

// Review
const reviewLoading = ref(false)
const selectedReview = ref<TerminalReview | null>(null)
const verifierRuns = ref<VerifierRun[]>([])

// Conversation
const convLoading = ref(false)
const convTurns = ref<ConversationTurn[]>([])

// Raw log
const rawLogLoading = ref(false)
const rawLogContent = ref('')
const rawLogSearch = ref('')

// Search
const searchQuery = ref('')
const searchResults = ref<TerminalAnalysis[]>([])
const searchLoading = ref(false)
const searchMode = ref(false)

const allTerminals = computed(() =>
  (state.value?.sessions ?? []).flatMap(s => s.terminals),
)

const topTools = computed(() => {
  if (!selected.value)
    return []
  return Object.entries(selected.value.tool_stats)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
})

const totalToolCalls = computed(() =>
  Object.values(selected.value?.tool_stats ?? {}).reduce((s, v) => s + v, 0),
)

const maxToolCount = computed(() =>
  Math.max(1, ...topTools.value.map(([, v]) => v)),
)

const displayedList = computed(() =>
  searchMode.value ? searchResults.value : analyses.value,
)

// Highlighted raw log: wrap search matches in a span
const highlightedRawLog = computed(() => {
  if (!rawLogContent.value)
    return ''
  if (!rawLogSearch.value)
    return escapeHtml(rawLogContent.value)
  return escapeHtml(rawLogContent.value).replace(
    new RegExp(escapeHtml(rawLogSearch.value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'),
    m => `<mark class="bg-yellow-200 dark:bg-yellow-800 rounded px-0.5">${m}</mark>`,
  )
})

const rawLogMatchCount = computed(() => {
  if (!rawLogSearch.value || !rawLogContent.value)
    return 0
  const re = new RegExp(rawLogSearch.value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')
  return (rawLogContent.value.match(re) || []).length
})

onMounted(async () => {
  await Promise.all([loadAnalyses(), loadDashboard()])
})

async function loadAnalyses(silent = false) {
  loading.value = !silent
  try {
    analyses.value = await apiConductor.terminalAnalyses(100)
    if (selected.value) {
      selected.value = analyses.value.find(a => a.id === selected.value?.id) ?? null
    }
  }
  finally {
    loading.value = false
  }
}

async function loadDashboard() {
  try {
    state.value = await apiConductor.dashboardState(true)
  }
  catch {}
}

async function triggerAnalysis(terminal: Terminal) {
  analysisLoading[terminal.id] = true
  try {
    const result = await apiConductor.triggerAnalysis(terminal.id, true)
    faToast.success(text('Analysis complete.', '分析完成。'))
    await loadAnalyses(true)
    await selectAnalysis(result)
  }
  catch {
    faToast.error(text('Analysis failed.', '分析失败。'))
  }
  finally {
    analysisLoading[terminal.id] = false
  }
}

async function selectAnalysis(a: TerminalAnalysis) {
  selected.value = a
  activeTab.value = 'analysis'
  selectedReview.value = null
  verifierRuns.value = []
  convTurns.value = []
  rawLogContent.value = ''
}

async function loadReview(force = false) {
  if (!selected.value)
    return
  reviewLoading.value = true
  try {
    selectedReview.value = force
      ? await apiConductor.runTerminalReview(selected.value.terminal_id, true)
      : await apiConductor.terminalReview(selected.value.terminal_id)
    if (selectedReview.value.work_item_id)
      verifierRuns.value = await apiConductor.verifierRuns(selectedReview.value.work_item_id)
    if (force) {
      faToast.success(text('Review complete.', '审查完成。'))
      await loadAnalyses(true)
    }
  }
  catch {
    selectedReview.value = null
    verifierRuns.value = []
    if (force)
      faToast.error(text('Review failed.', '审查失败。'))
  }
  finally {
    reviewLoading.value = false
  }
}

async function loadConversation() {
  if (!selected.value)
    return
  convLoading.value = true
  try {
    const res = await apiConductor.terminalConversation(selected.value.terminal_id)
    convTurns.value = res.turns
  }
  catch {
    faToast.error(text('Failed to load conversation.', '加载对话失败。'))
  }
  finally {
    convLoading.value = false
  }
}

async function loadRawLog() {
  if (!selected.value)
    return
  rawLogLoading.value = true
  try {
    const res = await apiConductor.terminalRawLog(selected.value.terminal_id)
    rawLogContent.value = res.raw_log
  }
  catch {
    faToast.error(text('Failed to load log.', '加载日志失败。'))
  }
  finally {
    rawLogLoading.value = false
  }
}

watch(activeTab, async (tab) => {
  if (tab === 'conversation' && convTurns.value.length === 0 && selected.value)
    await loadConversation()
  if (tab === 'raw-log' && !rawLogContent.value && selected.value)
    await loadRawLog()
  if (tab === 'review' && selected.value && !selectedReview.value)
    await loadReview(false)
})

async function doSearch() {
  if (!searchQuery.value.trim()) {
    searchMode.value = false
    return
  }
  searchLoading.value = true
  searchMode.value = true
  try {
    searchResults.value = await apiConductor.searchTerminalAnalyses(searchQuery.value.trim(), 30)
  }
  catch {
    faToast.error(text('Search failed.', '搜索失败。'))
  }
  finally {
    searchLoading.value = false
  }
}

function clearSearch() {
  searchQuery.value = ''
  searchMode.value = false
  searchResults.value = []
}

function formatDate(value: string) {
  return new Date(value).toLocaleString()
}

function totalToolCallsOf(a: TerminalAnalysis) {
  return Object.values(a.tool_stats).reduce((s, v) => s + v, 0)
}

function escapeHtml(s: string) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function turnBg(t: ConversationTurn) {
  if (t.type === 'human')
    return 'bg-primary/10 border-primary/30'
  if (t.type === 'tool_call')
    return 'bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-800'
  if (t.type === 'tool_result')
    return 'bg-muted/30 border-muted/60'
  return 'bg-background border-border'
}

function turnIcon(t: ConversationTurn) {
  if (t.type === 'human')
    return 'i-lucide:user'
  if (t.type === 'tool_call')
    return 'i-lucide:wrench'
  if (t.type === 'tool_result')
    return 'i-lucide:database'
  return 'i-lucide:bot'
}

function turnLabel(t: ConversationTurn) {
  if (t.type === 'human')
    return text('You', '用户')
  if (t.type === 'tool_call')
    return t.tool_name ?? 'Tool'
  if (t.type === 'tool_result')
    return text('Result', '返回结果')
  return text('Agent', '智能体')
}

function reviewStatusLabel(status?: string | null) {
  if (!status)
    return text('Not reviewed', '未审查')
  const map: Record<string, string> = {
    pending: text('Pending', '待审查'),
    running: text('Running', '审查中'),
    done: text('Done', '已审查'),
    skipped: text('Skipped', '已跳过'),
    error: text('Error', '错误'),
  }
  return map[status] ?? status
}

function reviewStatusClass(status?: string | null) {
  if (status === 'done')
    return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
  if (status === 'error')
    return 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
  if (status === 'skipped')
    return 'bg-zinc-100 text-zinc-600 dark:bg-zinc-900/40 dark:text-zinc-300'
  if (status === 'running')
    return 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
  return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
}

function verdictClass(verdict?: string | null) {
  const v = String(verdict || '').toLowerCase()
  if (v === 'pass' || v === 'passed' || v === 'met')
    return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
  if (v === 'fail' || v === 'failed' || v === 'missed')
    return 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
  return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
}

function checkClass(status?: string | null) {
  if (status === 'pass' || status === 'met')
    return 'text-emerald-600'
  if (status === 'fail' || status === 'missed' || status === 'error')
    return 'text-red-600'
  return 'text-amber-600'
}

function scoreClass(score?: number) {
  if ((score ?? 0) >= 80)
    return 'bg-emerald-500'
  if ((score ?? 0) >= 50)
    return 'bg-amber-500'
  return 'bg-red-500'
}

// Apply improvements dialog
const applyDialogOpen = ref(false)
const applyLoading = ref(false)
const applyForm = reactive({
  description: '',
  newCriteria: [] as string[],
})

function openApplyDialog() {
  const improvements
    = selectedReview.value?.review?.work_item_improvements
    ?? selected.value?.llm_review?.work_item_improvements
  if (!improvements)
    return
  applyForm.description = improvements.description ?? ''
  applyForm.newCriteria = [...(improvements.add_criteria ?? [])]
  applyDialogOpen.value = true
}

async function confirmApplyImprovements() {
  if (!selected.value?.work_item_id)
    return
  applyLoading.value = true
  try {
    const workItemId = selected.value.work_item_id
    const updatePayload: Parameters<typeof apiConductor.updateWorkItem>[1] = {}
    if (applyForm.description.trim())
      updatePayload.description = applyForm.description.trim()
    if (applyForm.newCriteria.length) {
      const currentItem = await apiConductor.getWorkItem(workItemId)
      const existingCriteria = currentItem.acceptance_criteria ?? []
      updatePayload.acceptance_criteria = [...existingCriteria, ...applyForm.newCriteria.filter(Boolean)]
    }
    await apiConductor.updateWorkItem(workItemId, updatePayload)
    faToast.success(text('Work item updated with review suggestions.', '工作项已按审查建议更新。'))
    applyDialogOpen.value = false
  }
  catch {
    faToast.error(text('Failed to update work item.', '工作项更新失败。'))
  }
  finally {
    applyLoading.value = false
  }
}
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-6 p-6">
    <!-- Header -->
    <div class="flex items-end justify-between">
      <div>
        <div class="text-sm text-muted-foreground">
          {{ text('Inspect agent behaviour and full conversations from terminal sessions', '查看智能体行为记录与完整对话历史') }}
        </div>
        <h1 class="mt-1 text-2xl font-semibold">
          {{ text('Terminal History', '终端历史分析') }}
        </h1>
      </div>
      <FaButton variant="outline" :loading="loading" @click="loadAnalyses()">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <!-- Trigger analysis for live terminals -->
    <section v-if="allTerminals.length" class="rounded-xl border bg-background p-4">
      <div class="mb-3 text-sm font-medium text-muted-foreground">
        {{ text('Run analysis on active terminal', '对运行中的终端触发分析') }}
      </div>
      <div class="flex flex-wrap gap-2">
        <FaButton
          v-for="terminal in allTerminals"
          :key="terminal.id"
          variant="outline"
          size="sm"
          :loading="analysisLoading[terminal.id]"
          @click="triggerAnalysis(terminal)"
        >
          <FaIcon name="i-lucide:scan-search" class="mr-1.5" />
          {{ terminal.window_name }}
        </FaButton>
      </div>
    </section>

    <!-- Search bar -->
    <section class="flex gap-2">
      <div class="relative flex-1">
        <FaIcon name="i-lucide:search" class="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <input
          v-model="searchQuery"
          class="h-9 w-full rounded-lg border bg-background pl-9 pr-4 text-sm outline-none focus:border-primary"
          :placeholder="text('Search across all history…', '搜索全部历史记录…')"
          @keydown.enter="doSearch"
          @keydown.escape="clearSearch"
        >
      </div>
      <FaButton size="sm" :loading="searchLoading" @click="doSearch">
        {{ text('Search', '搜索') }}
      </FaButton>
      <FaButton v-if="searchMode" size="sm" variant="outline" @click="clearSearch">
        {{ text('Clear', '清除') }}
      </FaButton>
    </section>

    <div v-if="searchMode" class="text-sm text-muted-foreground">
      {{ searchResults.length }} {{ text('results for', '条结果：') }} "{{ searchQuery }}"
    </div>

    <div class="grid gap-4 xl:grid-cols-[0.85fr_1.15fr]">
      <!-- Left: list -->
      <section class="space-y-2">
        <div v-if="!displayedList.length" class="rounded-xl border bg-background p-8 text-center text-muted-foreground">
          <span v-if="searchMode">{{ text('No matching records found.', '未找到匹配的记录。') }}</span>
          <span v-else>{{ text('No analyses yet. Trigger one from an active terminal above.', '暂无分析记录，请从上方运行中的终端触发。') }}</span>
        </div>
        <button
          v-for="a in displayedList"
          :key="a.id"
          class="w-full rounded-lg border bg-background p-3 text-left transition hover:border-primary"
          :class="selected?.id === a.id ? 'border-primary bg-primary/5' : ''"
          @click="selectAnalysis(a)"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0">
              <div class="truncate font-medium">{{ a.window_name || a.terminal_id }}</div>
              <div class="truncate text-xs text-muted-foreground">
                {{ a.session_name || '—' }} · {{ a.provider || '—' }}
              </div>
            </div>
            <span
              class="shrink-0 rounded px-1.5 py-0.5 text-xs font-medium"
              :class="a.risk_flags.length ? 'bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400' : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400'"
            >
              {{ a.risk_flags.length ? `${a.risk_flags.length} risk` : 'clean' }}
            </span>
          </div>
          <div class="mt-2 flex items-center gap-2">
            <span
              class="rounded px-1.5 py-0.5 text-xs font-medium"
              :class="reviewStatusClass(a.review_status)"
            >
              {{ reviewStatusLabel(a.review_status) }}
            </span>
            <span v-if="a.review_model" class="truncate text-xs text-muted-foreground">
              {{ a.review_model }}
            </span>
          </div>
          <div class="mt-2 grid grid-cols-4 gap-1 text-xs text-muted-foreground">
            <div class="rounded bg-muted/30 px-2 py-1">
              <div>{{ text('Tools', '工具') }}</div>
              <div class="font-medium text-foreground">{{ totalToolCallsOf(a) }}</div>
            </div>
            <div class="rounded bg-muted/30 px-2 py-1">
              <div>{{ text('Files', '文件') }}</div>
              <div class="font-medium text-foreground">{{ a.files_touched.length }}</div>
            </div>
            <div class="rounded bg-muted/30 px-2 py-1">
              <div>{{ text('Turns', '回合') }}</div>
              <div class="font-medium text-foreground">{{ a.conversation_turn_count }}</div>
            </div>
            <div class="rounded bg-muted/30 px-2 py-1">
              <div>{{ text('Lines', '行') }}</div>
              <div class="font-medium text-foreground">{{ a.line_count }}</div>
            </div>
          </div>
          <!-- Search excerpt -->
          <div v-if="a.match_excerpt" class="mt-1.5 truncate rounded bg-yellow-50 px-2 py-0.5 font-mono text-xs text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300">
            {{ a.match_excerpt }}
          </div>
          <div class="mt-1.5 text-xs text-muted-foreground">{{ formatDate(a.created_at) }}</div>
        </button>
      </section>

      <!-- Right: detail with tabs -->
      <section class="space-y-4">
        <div v-if="!selected" class="flex h-64 items-center justify-center rounded-xl border bg-background text-muted-foreground">
          {{ text('Select a record on the left to view details.', '请从左侧选择一条记录查看详情。') }}
        </div>

        <template v-else>
          <!-- Header card -->
          <div class="rounded-xl border bg-background p-4">
            <div class="flex items-start justify-between gap-3">
              <div>
                <h2 class="text-lg font-semibold">{{ selected.window_name || selected.terminal_id }}</h2>
                <div class="font-mono text-xs text-muted-foreground">{{ selected.terminal_id }}</div>
              </div>
              <div class="flex items-start gap-2">
                <FaButton size="sm" variant="outline" :loading="reviewLoading" @click="loadReview(true)">
                  <FaIcon name="i-lucide:badge-check" class="mr-1.5" />
                  {{ text('Run Review', '运行审查') }}
                </FaButton>
                <div class="text-right text-xs text-muted-foreground">
                  <div>{{ formatDate(selected.created_at) }}</div>
                  <div>{{ selected.line_count }} {{ text('log lines', '行日志') }}</div>
                </div>
              </div>
            </div>
            <div class="mt-3 grid grid-cols-3 gap-2 text-sm">
              <div>
                <div class="text-xs text-muted-foreground">{{ text('Session', '会话') }}</div>
                <div class="truncate">{{ selected.session_name || '—' }}</div>
              </div>
              <div>
                <div class="text-xs text-muted-foreground">{{ text('Provider', '运行方') }}</div>
                <div>{{ selected.provider || '—' }}</div>
              </div>
              <div>
                <div class="text-xs text-muted-foreground">{{ text('Work Item', '工作项') }}</div>
                <div class="truncate font-mono text-xs">{{ selected.work_item_id || '—' }}</div>
              </div>
            </div>
          </div>

          <!-- Tabs -->
          <div class="flex gap-1 rounded-lg border bg-muted/30 p-1">
            <button
              v-for="tab in (['analysis', 'review', 'conversation', 'raw-log'] as TabId[])"
              :key="tab"
              class="flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition"
              :class="activeTab === tab ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'"
              @click="activeTab = tab"
            >
              <span v-if="tab === 'analysis'">{{ text('Behaviour', '行为分析') }}</span>
              <span v-else-if="tab === 'review'">
                {{ text('Review', '审查报告') }}
                <span
                  class="ml-1 rounded px-1 text-xs"
                  :class="reviewStatusClass(selected.review_status)"
                >
                  {{ reviewStatusLabel(selected.review_status) }}
                </span>
              </span>
              <span v-else-if="tab === 'conversation'">
                {{ text('Conversation', '对话详情') }}
                <span v-if="selected.conversation_turn_count" class="ml-1 rounded bg-primary/10 px-1 text-xs text-primary">{{ selected.conversation_turn_count }}</span>
              </span>
              <span v-else>{{ text('Raw Log', '原始日志') }}</span>
            </button>
          </div>

          <!-- ── Tab: Behaviour ─────────────────────────────────────── -->
          <template v-if="activeTab === 'analysis'">
            <!-- Risk flags -->
            <div
              v-if="selected.risk_flags.length"
              class="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950/30"
            >
              <div class="mb-2 flex items-center gap-2 font-medium text-red-600 dark:text-red-400">
                <FaIcon name="i-lucide:triangle-alert" />
                {{ text('Risk Flags', '风险标记') }} ({{ selected.risk_flags.length }})
              </div>
              <ul class="space-y-1">
                <li
                  v-for="(flag, i) in selected.risk_flags"
                  :key="i"
                  class="rounded bg-red-100/60 px-2 py-1 font-mono text-xs text-red-700 dark:bg-red-900/40 dark:text-red-300"
                >
                  {{ flag }}
                </li>
              </ul>
            </div>
            <div
              v-else
              class="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm font-medium text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-400"
            >
              <FaIcon name="i-lucide:shield-check" />
              {{ text('No risk flags detected.', '未检测到风险行为。') }}
            </div>

            <!-- Tool call stats -->
            <div class="rounded-xl border bg-background p-4">
              <div class="mb-3 font-medium">
                {{ text('Tool Call Stats', '工具调用统计') }}
                <span class="ml-1 text-sm font-normal text-muted-foreground">({{ totalToolCalls }} total)</span>
              </div>
              <div v-if="topTools.length" class="space-y-2">
                <div v-for="[tool, count] in topTools" :key="tool" class="flex items-center gap-2 text-sm">
                  <div class="w-28 shrink-0 truncate text-muted-foreground">{{ tool }}</div>
                  <div class="flex-1 overflow-hidden rounded-full bg-muted/40">
                    <div
                      class="h-2 rounded-full bg-primary/70 transition-all"
                      :style="{ width: `${Math.round(count / maxToolCount * 100)}%` }"
                    />
                  </div>
                  <div class="w-8 text-right text-xs font-medium">{{ count }}</div>
                </div>
              </div>
              <div v-else class="text-sm text-muted-foreground">{{ text('No tool calls recorded.', '无工具调用记录。') }}</div>
            </div>

            <!-- Files touched -->
            <div class="rounded-xl border bg-background p-4">
              <div class="mb-2 font-medium">
                {{ text('Files Touched', '访问的文件') }}
                <span class="ml-1 text-sm font-normal text-muted-foreground">({{ selected.files_touched.length }})</span>
              </div>
              <div v-if="selected.files_touched.length" class="max-h-48 space-y-0.5 overflow-auto">
                <div
                  v-for="(file, i) in selected.files_touched"
                  :key="i"
                  class="truncate rounded px-2 py-0.5 font-mono text-xs hover:bg-muted/40"
                >
                  {{ file }}
                </div>
              </div>
              <div v-else class="text-sm text-muted-foreground">{{ text('None recorded.', '无记录。') }}</div>
            </div>

            <!-- Commands run -->
            <div class="rounded-xl border bg-background p-4">
              <div class="mb-2 font-medium">
                {{ text('Commands Run', '执行的命令') }}
                <span class="ml-1 text-sm font-normal text-muted-foreground">({{ selected.commands_run.length }})</span>
              </div>
              <div v-if="selected.commands_run.length" class="max-h-48 space-y-1 overflow-auto">
                <div
                  v-for="(cmd, i) in selected.commands_run"
                  :key="i"
                  class="truncate rounded bg-muted/30 px-2 py-1 font-mono text-xs"
                >
                  {{ cmd }}
                </div>
              </div>
              <div v-else class="text-sm text-muted-foreground">{{ text('No Bash commands recorded.', '无 Bash 命令记录。') }}</div>
            </div>

            <!-- Compliance summary -->
            <div v-if="selected.compliance_summary" class="rounded-xl border bg-background p-4">
              <div class="mb-3 font-medium">{{ text('Work Item Compliance', '工作项合规性') }}</div>
              <div class="mb-3 text-sm text-muted-foreground">{{ selected.compliance_summary.work_item_title }}</div>
              <div class="mb-4">
                <div class="mb-1 text-xs text-muted-foreground">{{ text('Files of Interest Coverage', '关注文件覆盖率') }}</div>
                <div class="flex items-center gap-3">
                  <div class="flex-1 overflow-hidden rounded-full bg-muted/40">
                    <div
                      class="h-2.5 rounded-full transition-all"
                      :class="
                        selected.compliance_summary.files_of_interest.pct >= 80
                          ? 'bg-emerald-500'
                          : selected.compliance_summary.files_of_interest.pct >= 40
                            ? 'bg-amber-500'
                            : 'bg-red-500'
                      "
                      :style="{ width: `${selected.compliance_summary.files_of_interest.pct}%` }"
                    />
                  </div>
                  <span class="text-sm font-medium">{{ selected.compliance_summary.files_of_interest.pct }}%</span>
                </div>
                <div class="mt-1 text-xs text-muted-foreground">
                  {{ selected.compliance_summary.files_of_interest.covered }}
                  / {{ selected.compliance_summary.files_of_interest.total }}
                  {{ text('files covered', '个文件已覆盖') }}
                </div>
              </div>
              <div>
                <div class="mb-2 text-xs text-muted-foreground">{{ text('Acceptance Criteria', '验收标准') }}</div>
                <ul class="space-y-1.5">
                  <li
                    v-for="(crit, i) in selected.compliance_summary.acceptance_criteria"
                    :key="i"
                    class="flex items-start gap-2 text-xs"
                  >
                    <FaIcon
                      :name="crit.evidence_found ? 'i-lucide:check-circle-2' : 'i-lucide:circle'"
                      class="mt-0.5 shrink-0"
                      :class="crit.evidence_found ? 'text-emerald-500' : 'text-muted-foreground'"
                    />
                    <span>{{ crit.criterion }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </template>

          <!-- ── Tab: Review ────────────────────────────────────────── -->
          <template v-else-if="activeTab === 'review'">
            <div v-if="reviewLoading" class="flex justify-center py-12">
              <FaIcon name="i-lucide:loader-2" class="animate-spin text-2xl text-muted-foreground" />
            </div>
            <template v-else>
              <div
                v-if="selectedReview?.review || selected.llm_review"
                class="space-y-4"
              >
                <div class="rounded-xl border bg-background p-4">
                  <div class="mb-3 flex items-start justify-between gap-4">
                    <div>
                      <div class="text-sm text-muted-foreground">{{ text('Semantic compliance', '语义合规性') }}</div>
                      <div class="mt-1 text-3xl font-semibold">
                        {{ selectedReview?.review?.compliance_score ?? selected.llm_review?.compliance_score ?? 0 }}
                      </div>
                    </div>
                    <div class="flex flex-wrap justify-end gap-2">
                      <span class="rounded px-2 py-1 text-xs font-medium" :class="verdictClass(selectedReview?.review?.verdict ?? selected.llm_review?.verdict)">
                        {{ selectedReview?.review?.verdict ?? selected.llm_review?.verdict }}
                      </span>
                      <span class="rounded bg-muted px-2 py-1 text-xs font-medium">
                        {{ selectedReview?.review?.risk_assessment ?? selected.llm_review?.risk_assessment }}
                      </span>
                    </div>
                  </div>
                  <div class="overflow-hidden rounded-full bg-muted/40">
                    <div
                      class="h-2.5 rounded-full"
                      :class="scoreClass(selectedReview?.review?.compliance_score ?? selected.llm_review?.compliance_score)"
                      :style="{ width: `${selectedReview?.review?.compliance_score ?? selected.llm_review?.compliance_score ?? 0}%` }"
                    />
                  </div>
                  <p class="mt-3 text-sm text-muted-foreground">
                    {{ selectedReview?.review?.deviation_summary ?? selected.llm_review?.deviation_summary }}
                  </p>
                  <div class="mt-2 text-xs text-muted-foreground">
                    {{ selected.review_model || text('No model recorded', '未记录模型') }}
                    <span v-if="selected.reviewed_at"> · {{ formatDate(selected.reviewed_at) }}</span>
                  </div>
                </div>

                <div class="rounded-xl border bg-background p-4">
                  <div class="mb-3 font-medium">{{ text('Acceptance Criteria Checks', '验收标准检查') }}</div>
                  <div class="space-y-2">
                    <div
                      v-for="(check, i) in (selectedReview?.review?.requirement_checks ?? selected.llm_review?.requirement_checks ?? [])"
                      :key="i"
                      class="rounded-lg border p-3"
                    >
                      <div class="flex items-start gap-2">
                        <FaIcon
                          :name="check.status === 'met' ? 'i-lucide:check-circle-2' : check.status === 'missed' ? 'i-lucide:x-circle' : 'i-lucide:circle-alert'"
                          class="mt-0.5 shrink-0"
                          :class="checkClass(check.status)"
                        />
                        <div class="min-w-0 flex-1">
                          <div class="font-medium">{{ check.criterion }}</div>
                          <div class="mt-1 text-sm text-muted-foreground">{{ check.evidence }}</div>
                          <div v-if="check.suggestion" class="mt-2 rounded bg-muted/40 px-2 py-1 text-xs">
                            {{ check.suggestion }}
                          </div>
                        </div>
                        <span class="shrink-0 rounded px-1.5 py-0.5 text-xs font-medium" :class="verdictClass(check.status)">
                          {{ check.status }}
                        </span>
                      </div>
                    </div>
                    <div
                      v-if="!(selectedReview?.review?.requirement_checks ?? selected.llm_review?.requirement_checks ?? []).length"
                      class="text-sm text-muted-foreground"
                    >
                      {{ text('No criterion-level checks were returned.', '未返回逐条验收检查。') }}
                    </div>
                  </div>
                </div>

                <div
                  v-if="(selectedReview?.review?.work_item_improvements ?? selected.llm_review?.work_item_improvements)"
                  class="rounded-xl border bg-background p-4"
                >
                  <div class="mb-3 font-medium">{{ text('Work Item Improvements', '工作项改进建议') }}</div>
                  <div class="space-y-3 text-sm">
                    <div v-if="(selectedReview?.review?.work_item_improvements ?? selected.llm_review?.work_item_improvements)?.description">
                      <div class="mb-1 text-xs text-muted-foreground">{{ text('Description', '描述') }}</div>
                      <div class="rounded bg-muted/40 p-2">
                        {{ (selectedReview?.review?.work_item_improvements ?? selected.llm_review?.work_item_improvements)?.description }}
                      </div>
                    </div>
                    <div v-if="(selectedReview?.review?.work_item_improvements ?? selected.llm_review?.work_item_improvements)?.add_criteria?.length">
                      <div class="mb-1 text-xs text-muted-foreground">{{ text('Add Criteria', '建议新增验收标准') }}</div>
                      <ul class="space-y-1">
                        <li
                          v-for="(item, i) in (selectedReview?.review?.work_item_improvements ?? selected.llm_review?.work_item_improvements)?.add_criteria"
                          :key="i"
                          class="rounded bg-muted/40 px-2 py-1"
                        >
                          {{ item }}
                        </li>
                      </ul>
                    </div>
                    <div v-if="(selectedReview?.review?.work_item_improvements ?? selected.llm_review?.work_item_improvements)?.clarify?.length">
                      <div class="mb-1 text-xs text-muted-foreground">{{ text('Clarify', '需要澄清') }}</div>
                      <ul class="space-y-1">
                        <li
                          v-for="(item, i) in (selectedReview?.review?.work_item_improvements ?? selected.llm_review?.work_item_improvements)?.clarify"
                          :key="i"
                          class="rounded bg-muted/40 px-2 py-1"
                        >
                          {{ item }}
                        </li>
                      </ul>
                    </div>
                  </div>
                  <div v-if="selected.work_item_id" class="mt-4 flex justify-end">
                    <FaButton size="sm" @click="openApplyDialog">
                      <FaIcon name="i-lucide:check-circle" class="mr-1.5" />
                      {{ text('Apply Improvements', '应用改进建议') }}
                    </FaButton>
                  </div>
                </div>

                <div class="rounded-xl border bg-background p-4">
                  <div class="mb-3 font-medium">{{ text('Verifier Runs', '验证轮次') }}</div>
                  <div v-if="verifierRuns.length" class="space-y-2">
                    <div v-for="run in verifierRuns" :key="run.id" class="rounded-lg border p-3">
                      <div class="flex items-center justify-between gap-2">
                        <div class="font-medium">#{{ run.attempt_no }} · {{ run.trigger_source }}</div>
                        <span class="rounded px-1.5 py-0.5 text-xs font-medium" :class="verdictClass(run.status)">
                          {{ run.status }}
                        </span>
                      </div>
                      <div v-if="run.summary" class="mt-1 text-sm text-muted-foreground">{{ run.summary }}</div>
                      <div v-if="run.checks.length" class="mt-2 grid gap-1">
                        <div v-for="check in run.checks" :key="check.id" class="flex items-center justify-between rounded bg-muted/30 px-2 py-1 text-xs">
                          <span>{{ check.name }} · {{ check.check_type }}</span>
                          <span :class="checkClass(check.status)">
                            {{ check.status }}
                            <template v-if="check.score !== null && check.score !== undefined"> · {{ check.score }}</template>
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div v-else class="text-sm text-muted-foreground">{{ text('No verifier runs recorded.', '暂无验证轮次。') }}</div>
                </div>
              </div>

              <div v-else class="rounded-xl border bg-background p-8 text-center">
                <div class="mx-auto mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
                  <FaIcon name="i-lucide:badge-help" class="text-xl text-muted-foreground" />
                </div>
                <div class="font-medium">{{ reviewStatusLabel(selected.review_status) }}</div>
                <div v-if="selected.review_error || selectedReview?.review_error" class="mt-1 text-sm text-muted-foreground">
                  {{ selected.review_error || selectedReview?.review_error }}
                </div>
                <FaButton class="mt-4" size="sm" :loading="reviewLoading" @click="loadReview(true)">
                  <FaIcon name="i-lucide:play" class="mr-1.5" />
                  {{ text('Run Review', '运行审查') }}
                </FaButton>
              </div>
            </template>
          </template>

          <!-- ── Tab: Conversation ──────────────────────────────────── -->
          <template v-else-if="activeTab === 'conversation'">
            <div v-if="convLoading" class="flex justify-center py-12">
              <FaIcon name="i-lucide:loader-2" class="animate-spin text-2xl text-muted-foreground" />
            </div>
            <div v-else-if="!convTurns.length" class="rounded-xl border bg-background p-8 text-center text-muted-foreground">
              {{ text('No conversation turns recorded. Re-run analysis to capture them.', '暂无对话回合记录，请重新触发分析以捕获。') }}
            </div>
            <div v-else class="space-y-2">
              <div class="mb-2 text-xs text-muted-foreground">
                {{ convTurns.length }} {{ text('turns', '条记录') }}
              </div>
              <div
                v-for="turn in convTurns"
                :key="turn.index"
                class="rounded-lg border p-3"
                :class="turnBg(turn)"
              >
                <!-- Turn header -->
                <div class="mb-1.5 flex items-center gap-1.5 text-xs font-medium">
                  <FaIcon :name="turnIcon(turn)" class="shrink-0" />
                  <span>{{ turnLabel(turn) }}</span>
                  <span v-if="turn.type === 'tool_call' && turn.tool_name" class="ml-1 rounded bg-amber-100 px-1 font-mono dark:bg-amber-900/40">{{ turn.tool_name }}</span>
                </div>
                <!-- Turn content -->
                <pre class="whitespace-pre-wrap break-all font-mono text-xs leading-relaxed">{{ turn.content }}</pre>
              </div>
            </div>
          </template>

          <!-- ── Tab: Raw Log ───────────────────────────────────────── -->
          <template v-else>
            <div v-if="rawLogLoading" class="flex justify-center py-12">
              <FaIcon name="i-lucide:loader-2" class="animate-spin text-2xl text-muted-foreground" />
            </div>
            <template v-else-if="rawLogContent">
              <!-- Search in log -->
              <div class="flex items-center gap-2">
                <div class="relative flex-1">
                  <FaIcon name="i-lucide:search" class="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <input
                    v-model="rawLogSearch"
                    class="h-8 w-full rounded-lg border bg-background pl-9 pr-4 text-sm outline-none focus:border-primary"
                    :placeholder="text('Search in log…', '在日志中搜索…')"
                  >
                </div>
                <span v-if="rawLogSearch" class="shrink-0 text-xs text-muted-foreground">
                  {{ rawLogMatchCount }} {{ text('matches', '处匹配') }}
                </span>
              </div>
              <!-- Log content -->
              <!-- eslint-disable-next-line vue/no-v-html -->
              <pre
                class="max-h-[60vh] overflow-auto rounded-xl border bg-zinc-950 p-4 font-mono text-xs leading-relaxed text-zinc-200"
                v-html="highlightedRawLog"
              />
            </template>
            <div v-else class="rounded-xl border bg-background p-8 text-center text-muted-foreground">
              {{ text('No raw log stored for this terminal.', '该终端无原始日志记录。') }}
            </div>
          </template>
        </template>
      </section>
    </div>

    <!-- Apply Improvements Dialog -->
    <div
      v-if="applyDialogOpen"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="applyDialogOpen = false"
    >
      <div class="mx-4 w-full max-w-lg rounded-xl border bg-background p-6 shadow-xl">
        <div class="mb-4 text-lg font-semibold">
          {{ text('Apply Review Improvements', '应用审查改进建议') }}
        </div>
        <div class="space-y-4 text-sm">
          <div v-if="applyForm.description">
            <div class="mb-1 font-medium text-muted-foreground">{{ text('New description', '新描述') }}</div>
            <div class="rounded-lg bg-muted/40 p-3 text-sm leading-relaxed">{{ applyForm.description }}</div>
          </div>
          <div v-if="applyForm.newCriteria.length">
            <div class="mb-1 font-medium text-muted-foreground">{{ text('Criteria to add', '新增验收标准') }}</div>
            <ul class="space-y-1">
              <li
                v-for="(c, i) in applyForm.newCriteria"
                :key="i"
                class="flex items-start gap-2 rounded bg-emerald-50 px-3 py-1.5 dark:bg-emerald-950/30"
              >
                <FaIcon name="i-lucide:plus" class="mt-0.5 shrink-0 text-emerald-600" />
                {{ c }}
              </li>
            </ul>
          </div>
          <div v-if="!applyForm.description && !applyForm.newCriteria.length" class="text-muted-foreground">
            {{ text('No improvements to apply.', '没有可应用的改进建议。') }}
          </div>
        </div>
        <div class="mt-6 flex justify-end gap-3">
          <FaButton variant="outline" size="sm" @click="applyDialogOpen = false">
            {{ text('Cancel', '取消') }}
          </FaButton>
          <FaButton
            size="sm"
            :loading="applyLoading"
            :disabled="!applyForm.description && !applyForm.newCriteria.length"
            @click="confirmApplyImprovements"
          >
            <FaIcon name="i-lucide:save" class="mr-1.5" />
            {{ text('Confirm & Save', '确认保存') }}
          </FaButton>
        </div>
      </div>
    </div>
  </div>
</template>
