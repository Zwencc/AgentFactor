<script setup lang="ts">
import apiSecurity from '@/api/modules/security'
import type { SecurityCapabilityProof, SecurityEndpoint, SecurityIntegration } from '@/api/modules/security'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'SecurityEndpoint',
})

const route = useRoute()
const router = useRouter()
const { text } = useLanguage()
const loading = ref(false)
const verifying = ref<string | null>(null)
const endpoint = ref<SecurityEndpoint | null>(null)

onMounted(load)

async function load() {
  loading.value = true
  try {
    const value = await apiSecurity.endpoint(String(route.params.endpointId))
    const integrations = await Promise.all(value.integrations.map(item => apiSecurity.integration(item.id)))
    endpoint.value = { ...value, integrations }
  }
  finally {
    loading.value = false
  }
}

async function verify(integration: SecurityIntegration) {
  verifying.value = integration.id
  try {
    const value = await apiSecurity.verify(integration.id)
    if (endpoint.value) {
      endpoint.value.integrations = endpoint.value.integrations.map(item => item.id === value.id ? value : item)
    }
    faToast.success(text('Capability verification completed', '能力验证完成'), {
      description: text(`Assurance: ${value.assurance}`, `保证状态：${value.assurance}`),
    })
  }
  finally {
    verifying.value = null
  }
}

function proofClass(status: SecurityCapabilityProof['status']) {
  if (status === 'pass')
    return 'border-emerald-400/40 bg-emerald-500/10 text-emerald-700'
  if (status === 'fail')
    return 'border-red-400/40 bg-red-500/10 text-red-600'
  return 'border-amber-400/40 bg-amber-500/10 text-amber-700'
}

function assuranceClass(assurance: string) {
  if (assurance === 'VERIFIED')
    return 'bg-emerald-500/10 text-emerald-700'
  if (assurance === 'DEGRADED')
    return 'bg-red-500/10 text-red-600'
  if (assurance === 'OBSERVED')
    return 'bg-blue-500/10 text-blue-700'
  return 'bg-muted text-muted-foreground'
}

function short(value?: string | null) {
  if (!value)
    return '-'
  return value.length > 18 ? `${value.slice(0, 18)}…` : value
}
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-6 p-6">
    <div class="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <button class="mb-3 flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground" @click="router.push('/security')">
          <FaIcon name="i-lucide:arrow-left" />
          {{ text('Security Overview', '安全总览') }}
        </button>
        <h1 class="text-3xl font-bold tracking-tight">
          {{ endpoint?.display_name || text('Endpoint Detail', '端点详情') }}
        </h1>
        <div class="mt-1 text-sm text-muted-foreground">
          {{ text('Read-only capability evidence; verification is not an enforcement claim', '只读能力证据；验证通过不等于建立了强制执行边界') }}
        </div>
      </div>
      <FaButton variant="outline" :loading="loading" @click="load">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <div v-if="endpoint" class="grid gap-4 sm:grid-cols-3">
      <div class="rounded-xl border bg-background p-4">
        <div class="text-xs text-muted-foreground">{{ text('Endpoint status', '端点状态') }}</div>
        <div class="mt-2 text-xl font-semibold">{{ endpoint.status }}</div>
      </div>
      <div class="rounded-xl border bg-background p-4">
        <div class="text-xs text-muted-foreground">{{ text('Runtime version', '运行时版本') }}</div>
        <div class="mt-2 font-mono text-sm">{{ endpoint.runtime_version || '-' }}</div>
      </div>
      <div class="rounded-xl border bg-background p-4">
        <div class="text-xs text-muted-foreground">{{ text('Protocol', '协议') }}</div>
        <div class="mt-2 font-mono text-sm">{{ endpoint.protocol_version || '-' }}</div>
      </div>
    </div>

    <section v-for="integration in endpoint?.integrations || []" :key="integration.id" class="space-y-5 rounded-xl border bg-background p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-lg font-semibold">{{ integration.name }}</h2>
          <div class="mt-1 font-mono text-xs text-muted-foreground">{{ integration.workspace_id }}</div>
        </div>
        <div class="flex items-center gap-2">
          <span class="rounded px-2 py-1 text-xs" :class="assuranceClass(integration.assurance)">{{ integration.assurance }}</span>
          <FaButton size="sm" :loading="verifying === integration.id" @click="verify(integration)">
            <FaIcon name="i-lucide:scan-search" class="mr-2" />
            {{ text('Verify capabilities', '验证能力') }}
          </FaButton>
        </div>
      </div>

      <div class="rounded-lg border border-amber-400/30 bg-amber-500/5 p-3 text-xs text-amber-800">
        {{ text('Receipts are local and unsigned, expire after 15 minutes, and never upgrade this endpoint to ENFORCED.', '回执为本机未签名证据，15 分钟后失效，且不会把端点升级为 ENFORCED。') }}
      </div>

      <div v-if="integration.proofs.length" class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <article v-for="proof in integration.proofs" :key="proof.capability" class="rounded-lg border p-4">
          <div class="flex items-start justify-between gap-2">
            <div class="font-mono text-sm font-medium">{{ proof.capability }}</div>
            <span class="rounded border px-2 py-0.5 text-xs uppercase" :class="proofClass(proof.status)">{{ proof.status }}</span>
          </div>
          <div class="mt-2 text-xs text-muted-foreground">{{ proof.code }}</div>
          <div v-if="Object.keys(proof.metrics).length" class="mt-3 flex flex-wrap gap-2">
            <span v-for="(metric, key) in proof.metrics" :key="key" class="rounded bg-muted px-2 py-1 text-xs">{{ key }}: {{ metric }}</span>
          </div>
          <div class="mt-3 text-[11px] text-muted-foreground">
            {{ text('Expires', '失效时间') }}: {{ proof.expires_at }}
          </div>
        </article>
      </div>
      <div v-else class="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
        {{ text('No capability receipt yet. Run verification to collect read-only evidence.', '尚无能力回执，请执行验证以采集只读证据。') }}
      </div>

      <div>
        <h3 class="mb-3 font-semibold">{{ text('Recent correlated evidence', '最近关联证据') }}</h3>
        <div class="overflow-x-auto rounded-lg border">
          <table class="w-full min-w-[760px] text-left text-xs">
            <thead class="border-b bg-muted/30 text-muted-foreground">
              <tr>
                <th class="px-3 py-2">{{ text('Event', '事件') }}</th>
                <th class="px-3 py-2">{{ text('Decision', '决策') }}</th>
                <th class="px-3 py-2">Correlation</th>
                <th class="px-3 py-2">Causation</th>
                <th class="px-3 py-2">Hash</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="event in (integration.recent_events || []).slice(0, 10)" :key="event.id" class="border-b last:border-0">
                <td class="px-3 py-2 font-mono">{{ event.event_type }}</td>
                <td class="px-3 py-2">{{ event.decision_kind || '-' }}</td>
                <td class="px-3 py-2 font-mono" :title="event.correlation_id || ''">{{ short(event.correlation_id) }}</td>
                <td class="px-3 py-2 font-mono" :title="event.causation_event_id || ''">{{ short(event.causation_event_id) }}</td>
                <td class="px-3 py-2 font-mono" :title="event.event_hash">{{ short(event.event_hash) }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="!(integration.recent_events || []).length" class="p-5 text-center text-sm text-muted-foreground">
            {{ text('No minimized event evidence.', '暂无最小化事件证据。') }}
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
