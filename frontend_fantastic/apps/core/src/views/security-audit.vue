<script setup lang="ts">
import apiSecurity from '@/api/modules/security'
import type {
  SecurityActionDecision,
  SecurityEvent,
  SecurityRiskAlert,
} from '@/api/modules/security'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'SecurityAudit',
})

type AuditSource = 'gateway' | 'acr' | 'alert'

interface AuditRow {
  key: string
  source: AuditSource
  timestamp: string
  outcome: string
  subject: string
  context: string
  policy: string
  evidence: string
  correlation: string
}

const { text } = useLanguage()
const loading = ref(false)
const events = ref<SecurityEvent[]>([])
const decisions = ref<SecurityActionDecision[]>([])
const alerts = ref<SecurityRiskAlert[]>([])
const sourceFilter = ref<'all' | AuditSource>('all')
const outcomeFilter = ref('all')

const rows = computed<AuditRow[]>(() => [
  ...decisions.value.map(decision => ({
    key: `gateway:${decision.id}`,
    source: 'gateway' as const,
    timestamp: decision.created_at,
    outcome: decision.effect,
    subject: `${decision.surface}.${decision.operation}`,
    context: `${decision.principal_id} · ${decision.reason_code}`,
    policy: `${decision.policy_version} · ${shortDigest(decision.policy_digest)}`,
    evidence: shortDigest(decision.action_fingerprint),
    correlation: decision.session_id || '-',
  })),
  ...events.value.map(event => ({
    key: `acr:${event.id}`,
    source: 'acr' as const,
    timestamp: event.occurred_at,
    outcome: event.decision_kind || 'observed',
    subject: event.event_type,
    context: `${event.integration_id} · ${event.agent_kind || event.actor_kind}`,
    policy: event.contract_ids.join(', ') || '-',
    evidence: shortDigest(event.event_hash),
    correlation: event.correlation_id ? shortDigest(event.correlation_id) : '-',
  })),
  ...alerts.value.map(alert => ({
    key: `alert:${alert.id}`,
    source: 'alert' as const,
    timestamp: alert.created_at,
    outcome: alert.effect,
    subject: text('Risk alert', '风险提醒'),
    context: alert.reason_code,
    policy: '-',
    evidence: shortDigest(alert.action_fingerprint),
    correlation: alert.source_event_id || alert.decision_id || '-',
  })),
].sort((left, right) => right.timestamp.localeCompare(left.timestamp)))

const outcomeOptions = computed(() => [...new Set(rows.value.map(row => row.outcome))].sort())

const filteredRows = computed(() => rows.value.filter((row) => {
  if (sourceFilter.value !== 'all' && row.source !== sourceFilter.value)
    return false
  return outcomeFilter.value === 'all' || row.outcome === outcomeFilter.value
}))

const blockedCount = computed(() => rows.value.filter(row => row.outcome === 'block' || row.outcome === 'deny').length)
const reminderCount = computed(() => alerts.value.filter(alert => alert.effect === 'remind').length)

onMounted(load)

async function load() {
  loading.value = true
  try {
    ;[events.value, decisions.value, alerts.value] = await Promise.all([
      apiSecurity.events(),
      apiSecurity.actionDecisions(),
      apiSecurity.alerts(),
    ])
  }
  finally {
    loading.value = false
  }
}

function shortDigest(value: string) {
  return value.length > 20 ? `${value.slice(0, 20)}…` : value
}

function sourceLabel(source: AuditSource) {
  if (source === 'gateway')
    return text('Gateway', '网关')
  if (source === 'acr')
    return 'ACR'
  return text('Alert', '提醒')
}

function sourceClass(source: AuditSource) {
  if (source === 'gateway')
    return 'bg-blue-500/10 text-blue-700'
  if (source === 'acr')
    return 'bg-violet-500/10 text-violet-700'
  return 'bg-amber-500/10 text-amber-700'
}

function outcomeClass(outcome: string) {
  if (outcome === 'block' || outcome === 'deny')
    return 'bg-red-500/10 text-red-600'
  if (outcome === 'remind' || outcome === 'ask' || outcome === 'repair')
    return 'bg-amber-500/10 text-amber-700'
  if (outcome === 'record' || outcome === 'pass' || outcome === 'rewrite')
    return 'bg-emerald-500/10 text-emerald-700'
  return 'bg-muted text-muted-foreground'
}
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-6 p-6">
    <div class="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <div class="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
          AgentFactor Security
        </div>
        <h1 class="mt-1 text-3xl font-bold tracking-tight">
          {{ text('Security Audit', '安全审计') }}
        </h1>
        <div class="mt-1 text-sm text-muted-foreground">
          {{ text('Gateway decisions, ACR events, and risk alerts without raw tool payloads', '统一查看网关决策、ACR 事件与风险提醒，不保留原始工具参数') }}
        </div>
      </div>
      <FaButton variant="outline" :loading="loading" @click="load">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <div class="rounded-xl border bg-background p-5">
        <div class="text-sm text-muted-foreground">{{ text('Audit records', '审计记录') }}</div>
        <div class="mt-2 text-3xl font-bold">{{ rows.length }}</div>
      </div>
      <div class="rounded-xl border bg-background p-5">
        <div class="text-sm text-muted-foreground">{{ text('Gateway decisions', '网关决策') }}</div>
        <div class="mt-2 text-3xl font-bold">{{ decisions.length }}</div>
      </div>
      <div class="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5">
        <div class="text-sm text-amber-700">{{ text('Reminders', '风险提醒') }}</div>
        <div class="mt-2 text-3xl font-bold text-amber-700">{{ reminderCount }}</div>
      </div>
      <div class="rounded-xl border border-red-500/30 bg-red-500/5 p-5">
        <div class="text-sm text-red-600">{{ text('Blocked or denied', '阻断或拒绝') }}</div>
        <div class="mt-2 text-3xl font-bold text-red-600">{{ blockedCount }}</div>
      </div>
    </div>

    <div class="flex flex-wrap gap-3 rounded-xl border bg-background p-4">
      <label class="flex items-center gap-2 text-sm">
        <span class="text-muted-foreground">{{ text('Source', '来源') }}</span>
        <select v-model="sourceFilter" class="rounded-md border bg-background px-3 py-2 text-sm">
          <option value="all">{{ text('All sources', '全部来源') }}</option>
          <option value="gateway">Gateway</option>
          <option value="acr">ACR</option>
          <option value="alert">{{ text('Risk alerts', '风险提醒') }}</option>
        </select>
      </label>
      <label class="flex items-center gap-2 text-sm">
        <span class="text-muted-foreground">{{ text('Outcome', '结果') }}</span>
        <select v-model="outcomeFilter" class="rounded-md border bg-background px-3 py-2 text-sm">
          <option value="all">{{ text('All outcomes', '全部结果') }}</option>
          <option v-for="outcome in outcomeOptions" :key="outcome" :value="outcome">
            {{ outcome }}
          </option>
        </select>
      </label>
    </div>

    <div class="overflow-hidden rounded-xl border bg-background">
      <div class="overflow-x-auto">
        <table class="w-full min-w-[1100px] text-left text-sm">
          <thead class="border-b bg-muted/30 text-xs text-muted-foreground">
            <tr>
              <th class="px-4 py-3">{{ text('Time', '时间') }}</th>
              <th class="px-4 py-3">{{ text('Source', '来源') }}</th>
              <th class="px-4 py-3">{{ text('Outcome', '结果') }}</th>
              <th class="px-4 py-3">{{ text('Event or action', '事件或动作') }}</th>
              <th class="px-4 py-3">{{ text('Context', '上下文') }}</th>
              <th class="px-4 py-3">{{ text('Policy or contracts', '策略或契约') }}</th>
              <th class="px-4 py-3">{{ text('Evidence', '证据') }}</th>
              <th class="px-4 py-3">Correlation</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in filteredRows" :key="row.key" class="border-b last:border-0">
              <td class="px-4 py-3 text-xs text-muted-foreground">{{ row.timestamp }}</td>
              <td class="px-4 py-3">
                <span class="rounded px-2 py-1 text-xs" :class="sourceClass(row.source)">{{ sourceLabel(row.source) }}</span>
              </td>
              <td class="px-4 py-3">
                <span class="rounded px-2 py-1 text-xs" :class="outcomeClass(row.outcome)">{{ row.outcome }}</span>
              </td>
              <td class="px-4 py-3 font-mono text-xs">{{ row.subject }}</td>
              <td class="px-4 py-3 text-xs">{{ row.context }}</td>
              <td class="px-4 py-3 font-mono text-xs">{{ row.policy }}</td>
              <td class="px-4 py-3 font-mono text-xs">{{ row.evidence }}</td>
              <td class="px-4 py-3 font-mono text-xs">{{ row.correlation }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="filteredRows.length === 0" class="p-8 text-center text-sm text-muted-foreground">
        {{ loading ? text('Loading audit records…', '正在加载审计记录…') : text('No matching audit records.', '没有匹配的审计记录。') }}
      </div>
    </div>
  </div>
</template>
