<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { CapabilityEstimate, TopologyProposal } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'ConductorTopology',
})

const loading = ref(false)
const { text } = useLanguage()
const deciding = reactive<Record<string, boolean>>({})
const statusFilter = ref('pending')
const proposals = ref<TopologyProposal[]>([])
const estimates = ref<CapabilityEstimate[]>([])
let refreshTimer: number | undefined

const filteredEstimates = computed(() => {
  return [...estimates.value].sort((a, b) => b.mean - a.mean)
})

const filterCounts = computed(() => {
  const all = proposals.value
  return {
    pending: all.filter(p => p.status === 'pending').length,
    accepted: all.filter(p => p.status === 'accepted').length,
    rejected: all.filter(p => p.status === 'rejected').length,
    all: all.length,
  }
})

onMounted(async () => {
  await load()
  refreshTimer = window.setInterval(() => {
    void load(true).catch(() => {})
  }, 10000)
})

onBeforeUnmount(() => {
  if (refreshTimer)
    window.clearInterval(refreshTimer)
})

async function load(silent = false) {
  loading.value = !silent
  try {
    const status = statusFilter.value === 'all' ? undefined : statusFilter.value
    const [proposalList, estimateList] = await Promise.all([
      apiConductor.topologyProposals(status),
      apiConductor.capabilityEstimates(),
    ])
    proposals.value = proposalList
    estimates.value = estimateList
  }
  finally {
    loading.value = false
  }
}

async function decide(item: TopologyProposal, accepted: boolean) {
  deciding[item.id] = true
  try {
    if (accepted) {
      await apiConductor.acceptTopologyProposal(item.id)
      faToast.success(text('Proposal accepted.', '已接受该调整建议。'))
    }
    else {
      await apiConductor.rejectTopologyProposal(item.id)
      faToast.success(text('Proposal rejected.', '已拒绝该调整建议。'))
    }
    await load()
  }
  finally {
    deciding[item.id] = false
  }
}

function setFilter(value: string) {
  statusFilter.value = value
  load()
}

function proposalTypeLabel(type: string) {
  const labels: Record<string, string> = {
    investigate: text('Investigate', '调查排查'),
    replace_provider: text('Replace provider', '更换执行方'),
    add_worker: text('Add worker', '增派智能体'),
  }
  return labels[type] ?? type
}

function proposalTypeColor(type: string) {
  const map: Record<string, string> = {
    investigate: 'bg-amber-500/10 text-amber-700 border-amber-400/30',
    replace_provider: 'bg-red-500/10 text-red-600 border-red-400/30',
    add_worker: 'bg-blue-500/10 text-blue-700 border-blue-400/30',
  }
  return map[type] ?? 'bg-muted text-muted-foreground border-border'
}

function proposalTypeIcon(type: string) {
  const map: Record<string, string> = {
    investigate: 'i-lucide:search',
    replace_provider: 'i-lucide:refresh-cw',
    add_worker: 'i-lucide:user-plus',
  }
  return map[type] ?? 'i-lucide:info'
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: text('Pending', '待决定'),
    accepted: text('Accepted', '已接受'),
    rejected: text('Rejected', '已拒绝'),
  }
  return labels[status] ?? status
}

function statusColor(status: string) {
  const map: Record<string, string> = {
    pending: 'bg-amber-500/10 text-amber-700',
    accepted: 'bg-emerald-500/10 text-emerald-700',
    rejected: 'bg-muted text-muted-foreground',
  }
  return map[status] ?? 'bg-muted text-muted-foreground'
}

function formatMetricParts(metrics: Record<string, unknown>) {
  const parts: { label: string, value: string, color?: string }[] = []
  if (typeof metrics.output_velocity_tpm === 'number') {
    const v = metrics.output_velocity_tpm
    parts.push({
      label: text('Velocity', '输出速度'),
      value: `${v.toFixed(1)} tpm`,
      color: v < 10 ? 'text-red-600' : v < 30 ? 'text-amber-600' : 'text-emerald-600',
    })
  }
  if (typeof metrics.error_density === 'number') {
    const v = metrics.error_density
    parts.push({
      label: text('Error rate', '错误率'),
      value: `${(v * 100).toFixed(0)}%`,
      color: v >= 0.3 ? 'text-red-600' : v >= 0.1 ? 'text-amber-600' : 'text-emerald-600',
    })
  }
  if (typeof metrics.idle_streak_minutes === 'number') {
    const v = metrics.idle_streak_minutes
    parts.push({
      label: text('Idle', '空闲时长'),
      value: `${v.toFixed(0)} ${text('min', '分钟')}`,
      color: v >= 15 ? 'text-amber-600' : 'text-muted-foreground',
    })
  }
  return parts
}

function capabilityBarColor(mean: number) {
  if (mean >= 0.7)
    return 'bg-emerald-500'
  if (mean >= 0.4)
    return 'bg-amber-500'
  return 'bg-red-500'
}
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-6 p-6">
    <!-- Header -->
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <div class="text-sm text-muted-foreground">
          {{ text('Adaptive team restructuring and provider reliability tracking', '自适应团队调整建议与执行方可靠性分析') }}
        </div>
        <h1 class="mt-1 text-2xl font-semibold">
          {{ text('Topology', '团队拓扑') }}
        </h1>
      </div>
      <FaButton variant="outline" :loading="loading" @click="load">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <!-- Filter tabs -->
    <div class="grid gap-4 sm:grid-cols-4">
      <button
        v-for="item in [
          { label: text('Pending', '待决定'), value: 'pending', count: filterCounts.pending },
          { label: text('Accepted', '已接受'), value: 'accepted', count: filterCounts.accepted },
          { label: text('Rejected', '已拒绝'), value: 'rejected', count: filterCounts.rejected },
          { label: text('All', '全部'), value: 'all', count: filterCounts.all },
        ]"
        :key="item.value"
        class="rounded-lg border bg-background p-4 text-left transition hover:border-primary/50"
        :class="statusFilter === item.value ? 'border-primary bg-primary/5' : ''"
        @click="setFilter(item.value)"
      >
        <div class="text-sm text-muted-foreground">
          {{ item.label }}
        </div>
        <div class="mt-2 text-2xl font-semibold">
          {{ item.count }}
        </div>
      </button>
    </div>

    <div class="grid gap-4 xl:grid-cols-[1fr_400px]">
      <!-- Proposals list -->
      <section class="space-y-4">
        <article
          v-for="proposal in proposals"
          :key="proposal.id"
          class="rounded-lg border bg-background p-5"
        >
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="flex items-start gap-3">
              <div class="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg border" :class="proposalTypeColor(proposal.proposal_type)">
                <FaIcon :name="proposalTypeIcon(proposal.proposal_type)" class="size-4" />
              </div>
              <div>
                <div class="font-semibold">
                  {{ proposalTypeLabel(proposal.proposal_type) }}
                </div>
                <div class="mt-0.5 text-xs text-muted-foreground">
                  {{ text('Terminal', '终端') }} {{ proposal.terminal_id.slice(0, 14) }}…
                </div>
              </div>
            </div>
            <span class="rounded-full px-2.5 py-0.5 text-xs font-medium" :class="statusColor(proposal.status)">
              {{ statusLabel(proposal.status) }}
            </span>
          </div>

          <!-- Reason -->
          <p class="mt-4 text-sm text-muted-foreground">
            {{ proposal.reason }}
          </p>

          <!-- Suggestions -->
          <div
            v-if="proposal.suggested_provider || proposal.suggested_persona"
            class="mt-3 grid gap-3 text-sm md:grid-cols-2"
          >
            <div v-if="proposal.suggested_provider" class="rounded-lg border p-3">
              <div class="text-xs text-muted-foreground">
                {{ text('Suggested Provider', '建议执行方') }}
              </div>
              <div class="mt-1 font-medium">
                {{ proposal.suggested_provider }}
              </div>
            </div>
            <div v-if="proposal.suggested_persona" class="rounded-lg border p-3">
              <div class="text-xs text-muted-foreground">
                {{ text('Suggested Persona', '建议角色') }}
              </div>
              <div class="mt-1 font-medium">
                {{ proposal.suggested_persona }}
              </div>
            </div>
          </div>

          <!-- Key metrics -->
          <div
            v-if="Object.keys(proposal.metrics_snapshot).length"
            class="mt-3 flex flex-wrap gap-3"
          >
            <div
              v-for="part in formatMetricParts(proposal.metrics_snapshot)"
              :key="part.label"
              class="rounded-lg border bg-muted/30 px-3 py-2 text-xs"
            >
              <div class="text-muted-foreground">
                {{ part.label }}
              </div>
              <div class="mt-1 font-semibold" :class="part.color">
                {{ part.value }}
              </div>
            </div>
          </div>

          <div class="mt-3 text-xs text-muted-foreground">
            {{ text('Created', '创建') }} {{ proposal.created_at }}
            <span v-if="proposal.decided_at"> · {{ text('Decided', '决策') }} {{ proposal.decided_at }}</span>
          </div>

          <div v-if="proposal.status === 'pending'" class="mt-4 flex gap-2">
            <FaButton :loading="deciding[proposal.id]" @click="decide(proposal, true)">
              <FaIcon name="i-lucide:check" class="mr-2" />
              {{ text('Accept', '接受') }}
            </FaButton>
            <FaButton variant="outline" :loading="deciding[proposal.id]" @click="decide(proposal, false)">
              <FaIcon name="i-lucide:x" class="mr-2" />
              {{ text('Reject', '拒绝') }}
            </FaButton>
          </div>
        </article>

        <div
          v-if="proposals.length === 0"
          class="flex flex-col items-center rounded-lg border bg-background p-10 text-muted-foreground"
        >
          <FaIcon name="i-lucide:network" class="mb-3 size-10 opacity-30" />
          <div class="text-sm">
            {{ text('No topology proposals in this filter.', '当前筛选条件下没有团队调整建议。') }}
          </div>
          <div class="mt-1 text-xs">
            {{ text('Proposals are generated automatically when performance thresholds are breached.', '当终端性能低于阈值时，系统会自动生成调整建议。') }}
          </div>
        </div>
      </section>

      <!-- Capability estimates -->
      <aside class="rounded-lg border bg-background p-4">
        <div class="mb-4 flex items-center justify-between">
          <div>
            <h2 class="text-lg font-semibold">
              {{ text('Capability Estimates', '执行能力评估') }}
            </h2>
            <div class="text-xs text-muted-foreground">
              {{ text('Bayesian success rate by provider/persona/task', '基于贝叶斯统计的成功率估计') }}
            </div>
          </div>
          <span class="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">{{ estimates.length }}</span>
        </div>
        <div class="space-y-3">
          <div
            v-for="estimate in filteredEstimates"
            :key="`${estimate.provider}-${estimate.persona}-${estimate.task_type}`"
            class="rounded-lg border p-3"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <div class="truncate text-sm font-medium">
                  {{ estimate.provider }} / {{ estimate.persona }}
                </div>
                <div class="text-xs text-muted-foreground">
                  {{ estimate.task_type }}
                </div>
              </div>
              <div class="shrink-0 text-right">
                <div class="text-lg font-bold" :class="estimate.mean >= 0.7 ? 'text-emerald-600' : estimate.mean >= 0.4 ? 'text-amber-600' : 'text-red-500'">
                  {{ Math.round(estimate.mean * 100) }}%
                </div>
                <div class="text-xs text-muted-foreground">
                  {{ estimate.total_attempts }} {{ text('attempts', '次') }}
                </div>
              </div>
            </div>
            <div class="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                class="h-full transition-all"
                :class="capabilityBarColor(estimate.mean)"
                :style="{ width: `${Math.round(estimate.mean * 100)}%` }"
              />
            </div>
            <div class="mt-1.5 text-xs text-muted-foreground">
              α {{ estimate.alpha.toFixed(1) }} / β {{ estimate.beta_param.toFixed(1) }}
              <span v-if="estimate.last_updated"> · {{ estimate.last_updated }}</span>
            </div>
          </div>
          <div
            v-if="filteredEstimates.length === 0"
            class="flex flex-col items-center py-8 text-muted-foreground"
          >
            <FaIcon name="i-lucide:bar-chart-2" class="mb-2 size-8 opacity-30" />
            <div class="text-sm">
              {{ text('No capability data yet.', '暂无执行能力数据。') }}
            </div>
            <div class="mt-1 text-xs">
              {{ text('Data accumulates as work items are completed.', '工作项完成后会自动积累数据。') }}
            </div>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>
