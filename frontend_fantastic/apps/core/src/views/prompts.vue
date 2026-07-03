<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { DashboardState, PromptItem } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'ConductorPrompts',
})

const router = useRouter()
const { text } = useLanguage()
const loading = ref(false)
const sending = reactive<Record<number, boolean>>({})
const responses = reactive<Record<number, string>>({})
const state = ref<DashboardState | null>(null)
let refreshTimer: number | undefined

const prompts = computed(() => state.value?.prompt_items ?? [])

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

async function sendPrompt(prompt: PromptItem) {
  const message = responses[prompt.id]?.trim()
  if (!prompt.target_id) {
    faToast.warning(text('This prompt has no detected target terminal.', '此提示没有检测到目标终端。'))
    return
  }
  if (!message) {
    faToast.warning(text('Enter a response before sending.', '请先输入回复。'))
    return
  }
  sending[prompt.id] = true
  try {
    await apiConductor.sendInput(prompt.target_id, { message })
    responses[prompt.id] = ''
    faToast.success(text('Response sent.', '回复已发送。'))
    await loadState()
  }
  finally {
    sending[prompt.id] = false
  }
}

function openTerminal(terminalId?: string | null) {
  if (!terminalId) {
    return
  }
  router.push({ path: '/sessions', query: { terminal: terminalId } })
}
</script>

<template>
  <div class="mx-auto p-6 max-w-7xl space-y-6">
    <div class="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <div class="text-sm text-muted-foreground">{{ text('Worker questions waiting for operator input', '需要人工回复的协作消息') }}</div>
        <h1 class="mt-1 text-2xl font-semibold">{{ text('Prompt Center', '协作提示') }}</h1>
      </div>
      <FaButton variant="outline" :loading="loading" @click="loadState">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <section class="grid gap-4">
      <article v-for="prompt in prompts" :key="prompt.id" class="border rounded-lg bg-background p-5">
        <div class="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="text-base font-semibold">{{ prompt.title }}</h2>
            <div class="mt-1 text-xs text-muted-foreground">
              Supervisor {{ prompt.supervisor_id }} · {{ prompt.created_at || 'unknown time' }}
            </div>
          </div>
          <FaButton
            v-if="prompt.target_id"
            variant="outline"
            size="sm"
            @click="openTerminal(prompt.target_id)"
          >
            <FaIcon name="i-lucide:terminal-square" class="mr-2" />
            {{ text('Console', '控制台') }}
          </FaButton>
        </div>

        <pre v-if="prompt.body" class="max-h-72 overflow-auto rounded-md bg-muted/40 p-4 text-xs">{{ prompt.body }}</pre>
        <div class="mt-3 text-sm text-muted-foreground">
          {{ text('Target:', '目标：') }}
          <code>{{ prompt.target_id || text('not detected', '未检测到') }}</code>
          <span v-if="prompt.target"> · {{ prompt.target.label }}</span>
        </div>

        <div v-if="prompt.target_id" class="mt-4 flex flex-col gap-3 md:flex-row">
          <FaInput
            v-model="responses[prompt.id]"
            class="flex-1"
            :placeholder="text('Response to send to worker', '发送给执行智能体的回复')"
            @keydown.enter.prevent="sendPrompt(prompt)"
          />
          <FaButton :loading="sending[prompt.id]" @click="sendPrompt(prompt)">
            {{ text('Send response', '发送回复') }}
          </FaButton>
        </div>
        <div v-else class="mt-4 rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-700">
          {{ text('Target terminal could not be parsed from this prompt. Open the supervisor session and respond manually.', '无法从此提示中解析目标终端。请打开主管会话并手动回复。') }}
        </div>
      </article>

      <div v-if="prompts.length === 0" class="border rounded-lg bg-background p-8 text-center text-muted-foreground">
        {{ text('No worker prompts awaiting attention.', '暂无需要处理的协作提示。') }}
      </div>
    </section>
  </div>
</template>
