<script setup lang="ts">
import apiSecurity from '@/api/modules/security'
import type { SecurityEndpoint, SecurityOverview } from '@/api/modules/security'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'SecurityOverview',
})

const router = useRouter()
const { text } = useLanguage()
const loading = ref(false)
const syncing = ref(false)
const overview = ref<SecurityOverview | null>(null)
const endpoints = ref<SecurityEndpoint[]>([])

onMounted(load)

async function load() {
  loading.value = true
  try {
    const [overviewValue, endpointValues] = await Promise.all([
      apiSecurity.overview(),
      apiSecurity.endpoints(),
    ])
    overview.value = overviewValue
    endpoints.value = endpointValues
  }
  finally {
    loading.value = false
  }
}

async function sync() {
  syncing.value = true
  try {
    const result = await apiSecurity.sync()
    faToast.success(text('ACR synchronized', 'ACR 同步完成'), {
      description: text(`${result.events_ingested} new events`, `新增 ${result.events_ingested} 条事件`),
    })
    await load()
  }
  finally {
    syncing.value = false
  }
}

function statusClass(status: string) {
  if (status === 'healthy')
    return 'border-emerald-400/40 bg-emerald-500/10 text-emerald-700'
  if (status === 'degraded' || status === 'conflict')
    return 'border-red-400/40 bg-red-500/10 text-red-600'
  return 'border-amber-400/40 bg-amber-500/10 text-amber-700'
}

function assuranceClass(assurance: string) {
  if (assurance.includes('ENFORCED') || assurance === 'GUARDED')
    return 'bg-emerald-500/10 text-emerald-700'
  if (assurance === 'DEGRADED')
    return 'bg-red-500/10 text-red-600'
  if (assurance === 'OBSERVED')
    return 'bg-blue-500/10 text-blue-700'
  return 'bg-muted text-muted-foreground'
}

function shortHash(value?: string | null) {
  if (!value)
    return '-'
  return `${value.slice(0, 12)}…`
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
          {{ text('Security Overview', '安全总览') }}
        </h1>
        <div class="mt-1 text-sm text-muted-foreground">
          {{ text('Verified runtime posture and minimized ACR evidence', '运行时安全姿态与最小化 ACR 证据') }}
        </div>
      </div>
      <div class="flex gap-2">
        <FaButton variant="outline" :loading="loading" @click="load">
          <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
          {{ text('Refresh', '刷新') }}
        </FaButton>
        <FaButton :loading="syncing" @click="sync">
          <FaIcon name="i-lucide:shield-check" class="mr-2" />
          {{ text('Sync ACR', '同步 ACR') }}
        </FaButton>
      </div>
    </div>

    <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
      <div v-for="item in [
        { label: text('Endpoints', '安全端点'), value: overview?.endpoints ?? 0, icon: 'i-lucide:server' },
        { label: text('Integrations', '接入工作区'), value: overview?.integrations ?? 0, icon: 'i-lucide:plug' },
        { label: text('Degraded', '降级接入'), value: overview?.degraded_integrations ?? 0, icon: 'i-lucide:shield-alert' },
        { label: text('Events', '安全事件'), value: overview?.events ?? 0, icon: 'i-lucide:activity' },
        { label: text('Reminders', '风险提醒'), value: overview?.reminders ?? 0, icon: 'i-lucide:bell-ring' },
        { label: text('Blocked', '已阻断'), value: overview?.blocked ?? 0, icon: 'i-lucide:ban' },
      ]" :key="item.label" class="rounded-xl border bg-background p-4">
        <div class="flex items-center gap-2 text-xs text-muted-foreground">
          <FaIcon :name="item.icon" class="size-4" />
          {{ item.label }}
        </div>
        <div class="mt-3 text-3xl font-bold">
          {{ item.value }}
        </div>
      </div>
    </div>

    <section class="rounded-xl border bg-background p-5">
      <div class="mb-4 flex items-center justify-between">
        <div>
          <h2 class="font-semibold">{{ text('Runtime posture', '运行时姿态') }}</h2>
          <div class="text-xs text-muted-foreground">
            {{ text('Observed is not the same as enforced', '观察到事件不代表已建立强制边界') }}
          </div>
        </div>
        <FaButton variant="outline" size="sm" @click="router.push('/security/audit')">
          {{ text('Security audit', '安全审计') }}
        </FaButton>
      </div>

      <div v-if="endpoints.length" class="space-y-4">
        <article v-for="endpoint in endpoints" :key="endpoint.id" class="rounded-lg border p-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div class="font-semibold">{{ endpoint.display_name }}</div>
              <div class="mt-1 text-xs text-muted-foreground">
                {{ endpoint.runtime_version || '-' }} · {{ endpoint.protocol_version || '-' }}
              </div>
            </div>
            <span class="rounded-md border px-2 py-1 text-xs" :class="statusClass(endpoint.status)">
              {{ endpoint.status }}
            </span>
          </div>
          <div class="mt-3">
            <FaButton variant="outline" size="sm" @click="router.push(`/security/endpoints/${encodeURIComponent(endpoint.id)}`)">
              {{ text('Endpoint detail', '端点详情') }}
            </FaButton>
          </div>
          <div v-if="endpoint.last_error" class="mt-3 rounded-md border border-red-400/30 bg-red-500/5 p-3 text-sm text-red-600">
            {{ endpoint.last_error }}
          </div>
          <div class="mt-4 grid gap-3 lg:grid-cols-2">
            <div v-for="integration in endpoint.integrations" :key="integration.id" class="rounded-lg bg-muted/30 p-4">
              <div class="flex items-center justify-between gap-3">
                <div class="font-medium">{{ integration.name }}</div>
                <span class="rounded px-2 py-1 text-xs" :class="assuranceClass(integration.assurance)">
                  {{ integration.assurance }}
                </span>
              </div>
              <div class="mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                <div>{{ text('Status', '状态') }}: {{ integration.status }}</div>
                <div>{{ text('Sequence', '事件序号') }}: {{ integration.last_sequence }}</div>
                <div>{{ text('Coverage gaps', '覆盖缺口') }}: {{ integration.unresolved_gaps }}</div>
                <div :title="integration.head_hash || ''">Head: {{ shortHash(integration.head_hash) }}</div>
              </div>
            </div>
          </div>
        </article>
      </div>
      <div v-else class="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
        {{ text('No ACR endpoint has been synchronized.', '尚未同步 ACR 安全端点。') }}
      </div>
    </section>
  </div>
</template>
