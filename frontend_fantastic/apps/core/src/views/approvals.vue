<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { Approval, DashboardState } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'ConductorApprovals',
})

const loading = ref(false)
const deciding = reactive<Record<number, boolean>>({})
const state = ref<DashboardState | null>(null)
const filter = ref<'PENDING' | 'APPROVED' | 'DENIED' | 'ALL'>('PENDING')
const denialReasons = reactive<Record<number, string>>({})
const router = useRouter()
const { text } = useLanguage()
let refreshTimer: number | undefined

const approvals = computed(() => {
  const items = state.value?.approvals ?? []
  if (filter.value === 'ALL') {
    return items
  }
  return items.filter(item => item.status === filter.value)
})

function setFilter(value: string) {
  filter.value = value as typeof filter.value
}

onMounted(async () => {
  await loadState()
  refreshTimer = window.setInterval(() => {
    void loadState(true).catch(() => {})
  }, 5000)
})

onBeforeUnmount(() => {
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
  }
})

async function loadState(silent = false) {
  loading.value = !silent
  try {
    state.value = await apiConductor.dashboardState(silent)
  }
  finally {
    loading.value = false
  }
}

function riskHints(command: string) {
  const hints = []
  if (/\brm\s+-rf\b/.test(command)) hints.push('rm -rf')
  if (/\bgit\s+reset\s+--hard\b/.test(command)) hints.push('git reset --hard')
  if (/\bsudo\b/.test(command)) hints.push('sudo')
  if (/\bcurl\b.*\|\s*(sh|bash)\b/.test(command)) hints.push('curl pipe shell')
  if (/\bpip(?:3)?\s+install\b.*--break-system-packages\b/.test(command)) hints.push('pip break-system-packages')
  return hints
}

async function approve(item: Approval) {
  deciding[item.id] = true
  try {
    await apiConductor.approve(item.id)
    faToast.success(text(`Approved #${item.id}`, `已批准 #${item.id}`))
    await loadState()
  }
  finally {
    deciding[item.id] = false
  }
}

async function deny(item: Approval) {
  deciding[item.id] = true
  try {
    await apiConductor.deny(item.id, denialReasons[item.id])
    faToast.success(text(`Denied #${item.id}`, `已拒绝 #${item.id}`))
    await loadState()
  }
  finally {
    deciding[item.id] = false
  }
}

async function copyCommand(command: string) {
  await navigator.clipboard?.writeText(command)
  faToast.success(text('Command copied.', '命令已复制。'))
}

function openTerminal(terminalId: string) {
  router.push({ path: '/sessions', query: { terminal: terminalId } })
}
</script>

<template>
  <div class="mx-auto p-6 max-w-7xl space-y-6">
    <div class="flex items-end justify-between">
      <div>
        <div class="text-sm text-muted-foreground">{{ text('Human-in-the-loop command review', '人工参与的命令审查') }}</div>
        <h1 class="mt-1 text-2xl font-semibold">{{ text('Approvals', '审批') }}</h1>
      </div>
      <FaButton variant="outline" :loading="loading" @click="loadState">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <div class="grid gap-4 sm:grid-cols-4">
      <button
        v-for="item in [
          { label: text('Pending', '待处理'), value: 'PENDING', count: state?.approvals_summary.pending ?? 0 },
          { label: text('Approved', '已批准'), value: 'APPROVED', count: state?.approvals_summary.approved ?? 0 },
          { label: text('Denied', '已拒绝'), value: 'DENIED', count: state?.approvals_summary.denied ?? 0 },
          { label: text('All', '全部'), value: 'ALL', count: state?.approvals_summary.total ?? 0 },
        ]"
        :key="item.value"
        class="rounded-lg border bg-background p-4 text-left"
        :class="filter === item.value ? 'border-primary bg-primary/5' : ''"
        @click="setFilter(item.value)"
      >
        <div class="text-sm text-muted-foreground">{{ item.label }}</div>
        <div class="mt-2 text-2xl font-semibold">{{ item.count }}</div>
      </button>
    </div>

    <div class="space-y-4">
      <article v-for="approval in approvals" :key="approval.id" class="border rounded-lg bg-background p-5">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div class="font-semibold">Request #{{ approval.id }}</div>
            <div class="text-xs text-muted-foreground">
              {{ approval.terminal?.label || approval.terminal_id }} -> supervisor {{ approval.supervisor?.label || approval.supervisor_id }}
            </div>
          </div>
          <span class="rounded bg-muted px-2 py-1 text-xs">{{ approval.status }}</span>
        </div>
        <div v-if="(approval.risk_hints?.length || riskHints(approval.command_text).length)" class="mb-3 rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600">
          Risk hints: {{ (approval.risk_hints?.length ? approval.risk_hints : riskHints(approval.command_text)).join(', ') }}
        </div>
        <pre class="overflow-auto rounded-md bg-muted/40 p-4 text-xs">{{ approval.command_text }}</pre>
        <div v-if="approval.metadata_payload" class="mt-3 text-sm text-muted-foreground">
          Metadata: {{ approval.metadata_payload }}
        </div>
        <div class="mt-3 text-xs text-muted-foreground">
          Created: {{ approval.created_at || '-' }} · Decided: {{ approval.decided_at || '-' }}
        </div>
        <div class="mt-4 flex flex-col gap-3 md:flex-row">
          <FaButton variant="outline" @click="openTerminal(approval.terminal_id)">
            <FaIcon name="i-lucide:terminal-square" class="mr-2" />
            {{ text('Console', '控制台') }}
          </FaButton>
          <FaButton variant="outline" @click="copyCommand(approval.command_text)">
            <FaIcon name="i-lucide:copy" class="mr-2" />
            {{ text('Copy command', '复制命令') }}
          </FaButton>
        </div>
        <div v-if="approval.status === 'PENDING'" class="mt-4 flex flex-col gap-3 md:flex-row">
          <FaInput v-model="denialReasons[approval.id]" class="flex-1" :placeholder="text('Denial reason', '拒绝原因')" />
          <FaButton :loading="deciding[approval.id]" @click="approve(approval)">{{ text('Approve', '批准') }}</FaButton>
          <FaButton variant="outline" :loading="deciding[approval.id]" @click="deny(approval)">{{ text('Deny', '拒绝') }}</FaButton>
        </div>
      </article>
      <div v-if="approvals.length === 0" class="border rounded-lg bg-background p-8 text-center text-muted-foreground">
        {{ text('No approvals in this filter.', '当前筛选下没有审批。') }}
      </div>
    </div>
  </div>
</template>
