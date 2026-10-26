<script setup lang="ts">
import apiSecurity from '@/api/modules/security'
import type { SecuritySandboxSession } from '@/api/modules/security'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'SecuritySandboxes',
})

const { text } = useLanguage()
const loading = ref(false)
const sessions = ref<SecuritySandboxSession[]>([])

onMounted(load)

async function load() {
  loading.value = true
  try {
    sessions.value = await apiSecurity.sandboxes()
  }
  finally {
    loading.value = false
  }
}

function statusClass(status: string) {
  if (status === 'active')
    return 'bg-blue-500/10 text-blue-700'
  if (status === 'completed')
    return 'bg-emerald-500/10 text-emerald-700'
  return 'bg-red-500/10 text-red-600'
}

function capabilityClass(status: string) {
  if (status === 'SANDBOX_ENFORCED' || status === 'GATEWAY_ENFORCED')
    return 'bg-emerald-500/10 text-emerald-700'
  if (status === 'NOT_ENFORCED')
    return 'bg-red-500/10 text-red-600'
  return 'bg-amber-500/10 text-amber-700'
}
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-6 p-6">
    <div class="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <div class="text-xs font-semibold uppercase tracking-widest text-muted-foreground">AgentFactor Security</div>
        <h1 class="mt-1 text-3xl font-bold tracking-tight">{{ text('Agent Sandboxes', 'Agent 沙箱') }}</h1>
        <div class="mt-1 text-sm text-muted-foreground">
          {{ text('Effective isolation evidence for public Agent sessions', '公共 Agent 会话的实际隔离证据') }}
        </div>
      </div>
      <FaButton variant="outline" :loading="loading" @click="load">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <div class="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm text-amber-700">
      {{ text(
        'Network remains shared for model connectivity. Codex MCP is gateway-only; Claude still exposes a first-party Docs connection, so its MCP status remains NOT_ENFORCED.',
        '为保证模型连接，网络仍与宿主共享。Codex MCP 已限定走网关；Claude 仍暴露厂商内置 Docs 连接，因此其 MCP 保持 NOT_ENFORCED。',
      ) }}
    </div>

    <article v-for="session in sessions" :key="session.id" class="rounded-xl border bg-background p-5">
      <div class="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <div class="flex items-center gap-2">
            <h2 class="font-mono text-sm font-semibold">{{ session.id }}</h2>
            <span class="rounded px-2 py-1 text-xs" :class="statusClass(session.status)">{{ session.status }}</span>
          </div>
          <div class="mt-1 text-xs text-muted-foreground">
            {{ session.provider }} · {{ session.backend }} · terminal {{ session.terminal_id }}
          </div>
        </div>
        <div class="text-xs text-muted-foreground">{{ session.started_at || session.created_at }}</div>
      </div>
      <div class="mt-4 grid gap-2 md:grid-cols-2">
        <div v-for="(value, name) in session.capabilities" :key="name" class="flex items-center justify-between rounded border px-3 py-2 text-xs">
          <span class="font-mono">{{ name }}</span>
          <span class="rounded px-2 py-1" :class="capabilityClass(value)">{{ value }}</span>
        </div>
      </div>
      <div v-if="session.last_error" class="mt-3 rounded border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600">
        {{ session.last_error }}
      </div>
    </article>

    <div v-if="sessions.length === 0" class="rounded-xl border bg-background p-10 text-center text-muted-foreground">
      {{ text('No sandbox sessions have been recorded.', '尚无沙箱会话记录。') }}
    </div>
  </div>
</template>
