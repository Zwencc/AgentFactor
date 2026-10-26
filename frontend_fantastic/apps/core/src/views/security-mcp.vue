<script setup lang="ts">
import apiSecurity from '@/api/modules/security'
import type { SecurityMCPServer } from '@/api/modules/security'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'SecurityMCP',
})

const { text } = useLanguage()
const loading = ref(false)
const discovering = ref(false)
const servers = ref<SecurityMCPServer[]>([])

onMounted(load)

async function load() {
  loading.value = true
  try {
    servers.value = await apiSecurity.mcpServers()
  }
  finally {
    loading.value = false
  }
}

async function discover() {
  discovering.value = true
  try {
    servers.value = await apiSecurity.discoverMcp()
  }
  finally {
    discovering.value = false
  }
}

function statusClass(status: string) {
  if (status === 'allowed' || status === 'healthy')
    return 'bg-emerald-500/10 text-emerald-700'
  if (status === 'schema_changed')
    return 'bg-amber-500/10 text-amber-700'
  return 'bg-red-500/10 text-red-600'
}

function shortDigest(value: string) {
  return value.length > 22 ? `${value.slice(0, 22)}…` : value
}
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-6 p-6">
    <div class="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <div class="text-xs font-semibold uppercase tracking-widest text-muted-foreground">AgentFactor Security</div>
        <h1 class="mt-1 text-3xl font-bold tracking-tight">{{ text('MCP Gateway', 'MCP 网关') }}</h1>
        <div class="mt-1 text-sm text-muted-foreground">
          {{ text('Unknown tools and unaccepted schema changes are blocked', '未知工具和未接受的 Schema 变化会被阻断') }}
        </div>
      </div>
      <div class="flex gap-2">
        <FaButton variant="outline" :loading="loading" @click="load">{{ text('Refresh', '刷新') }}</FaButton>
        <FaButton :loading="discovering" @click="discover">
          <FaIcon name="i-lucide:scan-search" class="mr-2" />
          {{ text('Discover now', '立即发现') }}
        </FaButton>
      </div>
    </div>

    <div class="rounded-lg border border-blue-500/30 bg-blue-500/5 p-4 text-sm text-blue-700">
      {{ text(
        'Server commands and allowlists come from the local security-gateway.json file; agents cannot register arbitrary upstream commands through this API.',
        'Server 命令与允许列表来自本机 security-gateway.json；Agent 不能通过 API 注册任意上游命令。',
      ) }}
    </div>

    <article v-for="server in servers" :key="server.id" class="rounded-xl border bg-background p-5">
      <div class="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <div class="flex items-center gap-2">
            <h2 class="text-lg font-semibold">{{ server.display_name }}</h2>
            <span class="rounded px-2 py-1 text-xs" :class="statusClass(server.status)">{{ server.status }}</span>
          </div>
          <div class="mt-1 font-mono text-xs text-muted-foreground">{{ server.id }} · {{ server.transport }}</div>
        </div>
        <div class="text-xs text-muted-foreground">{{ text('Last discovery', '最近发现') }}: {{ server.last_discovered_at || '-' }}</div>
      </div>
      <div v-if="server.last_error" class="mt-3 rounded border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600">
        {{ server.last_error }}
      </div>
      <div class="mt-4 overflow-x-auto">
        <table class="w-full min-w-[700px] text-left text-sm">
          <thead class="border-b text-xs text-muted-foreground">
            <tr>
              <th class="px-3 py-2">Tool</th>
              <th class="px-3 py-2">Status</th>
              <th class="px-3 py-2">Schema digest</th>
              <th class="px-3 py-2">{{ text('Last seen', '最近发现') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="tool in server.tools" :key="`${tool.name}:${tool.schema_digest}`" class="border-b last:border-0">
              <td class="px-3 py-3 font-mono text-xs">{{ tool.name }}</td>
              <td class="px-3 py-3"><span class="rounded px-2 py-1 text-xs" :class="statusClass(tool.status)">{{ tool.status }}</span></td>
              <td class="px-3 py-3 font-mono text-xs" :title="tool.schema_digest">{{ shortDigest(tool.schema_digest) }}</td>
              <td class="px-3 py-3 text-xs text-muted-foreground">{{ tool.last_seen_at }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="server.tools.length === 0" class="py-6 text-center text-sm text-muted-foreground">
          {{ text('No tools discovered.', '尚未发现工具。') }}
        </div>
      </div>
    </article>

    <div v-if="servers.length === 0" class="rounded-xl border bg-background p-10 text-center text-muted-foreground">
      {{ text('No MCP servers are configured.', '尚未配置 MCP Server。') }}
    </div>
  </div>
</template>
