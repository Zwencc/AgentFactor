<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { DashboardState, Persona, ProviderHealth } from '@/api/modules/conductor'
import DirectoryPickerModal from '@/components/DirectoryPickerModal/index.vue'
import { useLanguage } from '@/i18n'
import { useConductorProjectStore } from '@/store/modules/conductor/project'

defineOptions({
  name: 'ConductorTasks',
})

const router = useRouter()
const route = useRoute()
const { text } = useLanguage()
const projectStore = useConductorProjectStore()
const loading = ref(false)
const launching = ref(false)
const state = ref<DashboardState | null>(null)
const personas = ref<Persona[]>([])
const providers = ref<ProviderHealth[]>([])
const directoryPickerOpen = ref(false)
let refreshTimer: number | undefined

const task = reactive({
  name: '',
  workingDirectory: '',
  objective: '',
  supervisorProvider: 'claude_code',
  supervisorPersona: 'conductor',
  preset: 'engineering',
})

interface WorkerConfig {
  provider: string
  persona: string
  role: string
}

const workers = ref<WorkerConfig[]>([
  { provider: 'claude_code', persona: 'developer', role: 'Developer' },
  { provider: 'claude_code', persona: 'reviewer', role: 'Reviewer' },
  { provider: 'claude_code', persona: 'tester', role: 'Tester' },
])

const presetWorkers: Record<string, WorkerConfig[]> = {
  engineering: [
    { provider: 'claude_code', persona: 'developer', role: 'Developer' },
    { provider: 'claude_code', persona: 'reviewer', role: 'Reviewer' },
    { provider: 'claude_code', persona: 'tester', role: 'Tester' },
  ],
  qa: [
    { provider: 'deepseek', persona: 'deepseek_tester', role: 'QA' },
  ],
  documentation: [
    { provider: 'claude_code', persona: 'document_writer', role: 'Writer' },
  ],
  solo: [],
}

onMounted(async () => {
  if (route.query.title)
    task.name = route.query.title as string
  if (route.query.objective)
    task.objective = route.query.objective as string
  await Promise.all([loadState(), loadCatalogs()])
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

async function loadCatalogs() {
  const [personaRes, providerRes] = await Promise.all([
    apiConductor.personas(),
    apiConductor.providers(),
  ])
  personas.value = personaRes
  providers.value = providerRes
}


function applyPreset() {
  workers.value = (presetWorkers[task.preset] ?? []).map(item => ({ ...item }))
}

function personaProvider(personaName: string) {
  return personas.value.find(item => item.name === personaName)?.default_provider || 'claude_code'
}

function syncSupervisorProvider() {
  task.supervisorProvider = personaProvider(task.supervisorPersona)
}

function syncWorkerProvider(index: number) {
  workers.value[index].provider = personaProvider(workers.value[index].persona)
}

function addWorker() {
  workers.value.push({ provider: 'claude_code', persona: 'developer', role: 'Worker' })
}

function removeWorker(index: number) {
  workers.value.splice(index, 1)
}

async function launchTask() {
  if (!task.objective.trim()) {
    faToast.warning(text('Task objective is required.', '请填写任务目标。'))
    return
  }
  launching.value = true
  try {
    const workingDirectory = task.workingDirectory.trim() || null
    const supervisor = await apiConductor.createSession({
      provider: task.supervisorProvider,
      agent_profile: task.supervisorPersona,
      role: 'supervisor',
      working_directory: workingDirectory,
      workers: workers.value.map(worker => ({
        provider: worker.provider,
        agent_profile: worker.persona,
        role: 'worker',
        working_directory: workingDirectory,
      })),
    })
    await apiConductor.sendInput(supervisor.id, {
      message: task.name.trim() ? `Task: ${task.name}\n\n${task.objective}` : task.objective,
    })
    faToast.success(text('Task launched.', '任务已启动。'))
    const launchedItemId = route.query.workItemId as string | undefined
    task.name = ''
    task.objective = ''
    await loadState(true)
    router.push({ path: '/sessions', query: { terminal: supervisor.id } })
    if (launchedItemId) {
      apiConductor.updateWorkItem(launchedItemId, {
        owner_terminal_id: supervisor.id,
        status: 'in_progress',
      }).catch(() => {})
    }
  }
  finally {
    launching.value = false
  }
}
</script>

<template>
  <div class="mx-auto p-6 max-w-7xl space-y-6">
    <div class="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <div class="text-sm text-muted-foreground">
          {{ text('Create and hand off work to the local agent team', '创建任务并交给本地智能体团队执行') }}
        </div>
        <h1 class="mt-1 text-2xl font-semibold">{{ text('Task Center', '任务中心') }}</h1>
      </div>
      <FaButton variant="outline" :loading="loading" @click="loadState()">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <div class="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
      <section class="border rounded-lg bg-background p-5">
        <div class="mb-4 flex items-center justify-between">
          <h2 class="text-lg font-semibold">{{ text('New Task', '新建任务') }}</h2>
          <FaButton :loading="launching" @click="launchTask">
            <FaIcon name="i-lucide:play" class="mr-2" />
            {{ text('Start task', '启动任务') }}
          </FaButton>
        </div>
        <div class="mb-3 flex items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-sm">
          <FaIcon name="i-lucide:folder-open" class="size-4 text-muted-foreground" />
          <span class="text-muted-foreground">{{ text('Project', '项目') }}</span>
          <span class="font-medium">{{ projectStore.currentProject }}</span>
          <span class="ml-auto text-xs text-muted-foreground">{{ text('Change in topbar', '在顶栏切换') }}</span>
        </div>
        <div class="grid gap-4 md:grid-cols-2">
          <FaInput v-model="task.name" :placeholder="text('Task name', '任务名称')" />
          <div class="flex min-w-0 gap-2">
            <FaInput v-model="task.workingDirectory" class="min-w-0 flex-1" :placeholder="text('Working directory', '工作目录')" />
            <FaButton variant="outline" @click="directoryPickerOpen = true">
              <FaIcon name="i-lucide:folder-open" class="mr-2" />
              {{ text('Browse', '选择') }}
            </FaButton>
          </div>
        </div>
        <DirectoryPickerModal v-model:open="directoryPickerOpen" v-model="task.workingDirectory" />
        <textarea
          v-model="task.objective"
          class="mt-4 min-h-40 w-full border rounded-md bg-background p-3 text-sm outline-none focus:ring-2 focus:ring-primary/30"
          :placeholder="text('Describe the work objective for the conductor.', '描述要交给主管智能体的任务目标。')"
        />
        <div class="mt-4 grid gap-4 md:grid-cols-3">
          <select v-model="task.supervisorPersona" class="border rounded-md bg-background px-3 py-2" @change="syncSupervisorProvider">
            <option v-for="persona in personas" :key="persona.name" :value="persona.name">{{ persona.name }}</option>
          </select>
          <select v-model="task.supervisorProvider" class="border rounded-md bg-background px-3 py-2">
            <option v-for="provider in providers" :key="provider.key" :value="provider.key">{{ provider.label }}</option>
          </select>
          <select v-model="task.preset" class="border rounded-md bg-background px-3 py-2" @change="applyPreset">
            <option value="engineering">{{ text('Engineering', '工程开发') }}</option>
            <option value="qa">{{ text('QA', '测试验证') }}</option>
            <option value="documentation">{{ text('Documentation', '文档整理') }}</option>
            <option value="solo">{{ text('Solo supervisor', '仅启动主管') }}</option>
          </select>
        </div>

        <div class="mt-5 space-y-3">
          <div class="flex items-center justify-between">
            <h3 class="font-medium">{{ text('Workers', '执行智能体') }}</h3>
            <FaButton size="sm" variant="outline" @click="addWorker">{{ text('Add worker', '添加执行智能体') }}</FaButton>
          </div>
          <div v-for="(worker, index) in workers" :key="index" class="grid gap-3 rounded-md border p-3 md:grid-cols-[1fr_1fr_1fr_auto]">
            <select v-model="worker.persona" class="border rounded-md bg-background px-3 py-2" @change="syncWorkerProvider(index)">
              <option v-for="persona in personas" :key="persona.name" :value="persona.name">{{ persona.name }}</option>
            </select>
            <select v-model="worker.provider" class="border rounded-md bg-background px-3 py-2">
              <option v-for="provider in providers" :key="provider.key" :value="provider.key">{{ provider.label }}</option>
            </select>
            <FaInput v-model="worker.role" :placeholder="text('Role label', '职责标签')" />
            <FaButton variant="outline" @click="removeWorker(index)">{{ text('Remove', '移除') }}</FaButton>
          </div>
        </div>
      </section>

      <section class="border rounded-lg bg-background p-5">
        <div class="mb-4 flex items-center justify-between">
          <h2 class="text-lg font-semibold">{{ text('Active Sessions', '当前会话') }}</h2>
          <FaButton variant="outline" size="sm" @click="router.push('/sessions')">{{ text('Open sessions', '进入会话') }}</FaButton>
        </div>
        <div class="space-y-3">
          <div v-for="session in state?.sessions ?? []" :key="session.name" class="rounded-md border p-3">
            <div class="flex items-center justify-between gap-3">
              <div class="font-medium">{{ session.name }}</div>
              <span class="rounded bg-muted px-2 py-1 text-xs">{{ session.terminals.length }} terminals</span>
            </div>
            <div class="mt-2 text-xs text-muted-foreground">
              {{ [...new Set(session.terminals.map(t => t.provider))].join(', ') || '-' }}
            </div>
          </div>
          <div v-if="!state?.sessions.length" class="py-8 text-center text-muted-foreground">
            {{ text('No active sessions.', '暂无活动会话。') }}
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
