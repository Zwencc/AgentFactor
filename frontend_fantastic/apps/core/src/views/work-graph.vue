<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { BlueprintItem, BlueprintResult, DashboardState, Persona, ProviderHealth, ProofWindow, WorkEdge, WorkGraph, WorkGraphGenerationRequest, WorkItem } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'
import { useConductorProjectStore } from '@/store/modules/conductor/project'

defineOptions({
  name: 'ConductorWorkGraph',
})

const projectStore = useConductorProjectStore()
const router = useRouter()
const loading = ref(false)
const { text, language } = useLanguage()
const edgeLoading = ref(false)
const createLoading = ref(false)
const deleteLoading = ref(false)
const editMode = ref(false)
const editLoading = ref(false)
const showCreateForm = ref(false)
const projectId = computed({
  get: () => projectStore.currentProject,
  set: (v: string) => projectStore.setProject(v),
})
const statusFilter = ref('')
const graph = ref<WorkGraph | null>(null)
const selectedItemId = ref('')
const proofWindows = ref<ProofWindow[]>([])
const state = ref<DashboardState | null>(null)

// ── Auto-generate state ──────────────────────────────────────────────
const personas = ref<Persona[]>([])
const providers = ref<ProviderHealth[]>([])
const genModal = ref(false)
const genTab = ref<'new' | 'history'>('new')

const genTerminalId = ref('')
const blueprint = ref<BlueprintResult | null>(null)
const blueprintHistory = ref<BlueprintResult[]>([])
const generationHistory = ref<WorkGraphGenerationRequest[]>([])
const historyLoading = ref(false)
const requestHistoryLoading = ref(false)
const reviewModal = ref(false)
const selectedSlugs = ref<Set<string>>(new Set())
const importing = ref(false)
const generating = ref(false)
const showAdvanced = ref(false)

// ── Background polling (silent, no UI) ──────────────────────────────
let bgPollTimer: ReturnType<typeof setInterval> | undefined
let bgPollDeadline = 0
const BG_POLL_INTERVAL = 10_000
const BG_POLL_TIMEOUT = 900_000

function isReadyBlueprint(value: unknown): value is BlueprintResult {
  const candidate = value as Partial<BlueprintResult> & { ready?: boolean }
  return !!candidate
    && candidate.ready !== false
    && !candidate.error
    && Array.isArray(candidate.items)
    && candidate.items.length > 0
}

function stopBgPoll() {
  clearInterval(bgPollTimer)
  bgPollTimer = undefined
}

function startBgPoll() {
  stopBgPoll()
  bgPollDeadline = Date.now() + BG_POLL_TIMEOUT
  bgPollTimer = setInterval(async () => {
    if (Date.now() > bgPollDeadline) {
      stopBgPoll()
      return
    }
    const termId = genTerminalId.value
    const pid = projectId.value
    if (!termId || !pid) return

    const [fileResult, bpResult] = await Promise.allSettled([
      projectStore.currentRootDirectory ? apiConductor.blueprintFile(pid, true) : Promise.reject(new Error('no-dir')),
      apiConductor.pollBlueprint(termId),
    ])

    const file = fileResult.status === 'fulfilled' ? fileResult.value : null
    const bp = bpResult.status === 'fulfilled' ? bpResult.value : null

    const fileReady = file?.complete ?? false
    const bpError = bp && 'error' in bp ? bp.error : ''
    const bpReady = isReadyBlueprint(bp)

    if (bpError) {
      stopBgPoll()
      faToast.error(String(bpError))
      void loadGenerationHistory()
      return
    }

    if (fileReady && !bpReady) {
      void loadBlueprintHistory()
    }

    if (bpReady) {
      stopBgPoll()
      const finalBp = bp
      if (finalBp && !finalBp.error && (finalBp.items?.length ?? 0) > 0) {
        blueprint.value = finalBp
        selectedSlugs.value = new Set(finalBp.items.map((i: BlueprintItem) => i.id))
        reviewModal.value = true
        void loadBlueprintHistory()
        faToast.success(text('Blueprint ready — review and import.', '工作图谱已生成，请审核并导入。'))
      }
    }
  }, BG_POLL_INTERVAL)
}

const gen = reactive({
  description: '',
  constraints: '',
  persona: 'conductor',
  provider: 'claude_code',
  mcpCapable: false,
  baseBlueprintTerminalId: '',
})

// ── Description templates ────────────────────────────────────────────
const descriptionTemplates = [
  {
    icon: 'i-lucide:globe',
    label: 'Web API',
    labelZh: 'Web API',
    value: `项目类型：RESTful Web API 后端服务
核心功能：
- 用户认证（注册 / 登录 / JWT）
- 核心业务数据的增删改查接口
- 接口文档（OpenAPI / Swagger）
技术栈：（请填写，如 FastAPI + PostgreSQL）
目标用户：（请描述，如内部系统 / 第三方客户端）
特殊要求：（如高并发、多租户、数据隔离等）`,
  },
  {
    icon: 'i-lucide:monitor',
    label: 'Desktop',
    labelZh: '桌面应用',
    value: `项目类型：桌面端应用程序
核心功能：
- （请列出 3–5 个主要功能）

技术栈：（如 Python + Tkinter / PyQt，或 Electron + Vue）
目标平台：（Windows / macOS / Linux）
目标用户：（描述使用人群）
特殊要求：（如离线使用、系统托盘、快捷键支持等）`,
  },
  {
    icon: 'i-lucide:smartphone',
    label: 'Mobile',
    labelZh: '移动端',
    value: `项目类型：移动端应用
核心功能：
- （请列出 3–5 个主要功能）

技术栈：（如 Flutter / React Native / 原生 iOS & Android）
目标平台：（iOS / Android / 跨平台）
目标用户：（描述使用人群）
特殊要求：（如推送通知、离线支持、摄像头 / 地理位置权限等）`,
  },
  {
    icon: 'i-lucide:terminal',
    label: 'CLI Tool',
    labelZh: 'CLI 工具',
    value: `项目类型：命令行工具（CLI）
核心功能：
- （请列出主要子命令及功能）

技术栈：（如 Python + Click，或 Go / Rust）
目标用户：（如开发者、运维人员）
特殊要求：（如配置文件支持、管道 / 标准输入输出、跨平台等）`,
  },
  {
    icon: 'i-lucide:layout-dashboard',
    label: 'Frontend',
    labelZh: '前端应用',
    value: `项目类型：前端 Web 应用（SPA）
核心功能：
- （请列出主要页面和交互功能）

技术栈：（如 Vue 3 + Vite，或 React + Next.js）
接入接口：（如自研后端 API / 第三方 API）
目标用户：（描述使用人群）
特殊要求：（如响应式设计、暗色模式、国际化等）`,
  },
  {
    icon: 'i-lucide:database',
    label: 'Data Pipeline',
    labelZh: '数据管道',
    value: `项目类型：数据管道 / ETL 任务
核心功能：
- 数据源接入（来源：请描述）
- 数据清洗与转换逻辑
- 数据入库（目标：请描述）
- 调度与监控告警
技术栈：（如 Python + Airflow / dbt / Pandas）
数据规模：（如日增量、总量预估）
特殊要求：（如实时流式处理、数据质量校验、幂等重跑等）`,
  },
  {
    icon: 'i-lucide:layers',
    label: 'Full-Stack',
    labelZh: '全栈应用',
    value: `项目类型：全栈 Web 应用
核心功能（前端）：
- （请列出主要页面和交互）

核心功能（后端）：
- 用户认证与权限管理
- 核心业务 API
- 数据持久化
技术栈：（如 Vue 3 + FastAPI + PostgreSQL）
部署方式：（如 Docker / 云平台）
特殊要求：（如实时通信、文件上传、第三方登录等）`,
  },
  {
    icon: 'i-lucide:search',
    label: 'Research',
    labelZh: '调研分析',
    value: `项目类型：技术调研 / 原型验证
调研目标：（明确要验证的核心问题）
调研范围：
- 评估方案一：
- 评估方案二：
- 对比维度：（性能、易用性、成本、社区活跃度等）
输出形式：（如调研报告、Demo 代码、性能测试数据）
时间约束：（预计调研周期）`,
  },
]

// ── Constraint chips ─────────────────────────────────────────────────
const constraintChips = [
  { label: '测试覆盖率 ≥ 80%', value: '单元测试覆盖率不低于 80%，关键路径需有集成测试。' },
  { label: '用户认证与权限', value: '需要完整的用户注册 / 登录功能，以及基于角色的权限管理（RBAC）。' },
  { label: 'Docker 容器化', value: '提供 Dockerfile 和 docker-compose.yml，支持一键本地启动。' },
  { label: 'API 文档', value: '后端 API 必须有完整的 OpenAPI / Swagger 文档，并与代码保持同步。' },
  { label: 'CI/CD 流水线', value: '配置 GitHub Actions CI，提交代码时自动运行测试和代码风格检查。' },
  { label: '数据加密存储', value: '敏感数据（密码、令牌）必须加密存储，密码使用 bcrypt 或等效算法哈希。' },
  { label: '国际化支持', value: '界面文本需支持多语言，至少支持中文和英文切换（i18n）。' },
  { label: '日志与审计', value: '关键操作（登录、数据变更、删除）需记录结构化日志，保留至少 30 天。' },
  { label: '性能要求', value: '核心 API 接口的 P95 响应时间不超过 200ms（不含网络延迟）。' },
  { label: '错误监控', value: '未处理异常需上报到监控系统（Sentry 或等效），包含堆栈信息和上下文。' },
]

function applyDescriptionTemplate(tmpl: { value: string }) {
  gen.description = tmpl.value
}

function appendConstraint(value: string) {
  gen.constraints = gen.constraints.trimEnd()
    ? `${gen.constraints.trimEnd()}\n${value}`
    : value
}

function personaProvider(personaName: string) {
  return personas.value.find(p => p.name === personaName)?.default_provider || 'claude_code'
}

function providerMcpCapable(providerKey: string) {
  return providers.value.find(p => p.key === providerKey)?.mcp_capable ?? false
}

function syncProviderMcpCapable() {
  gen.mcpCapable = providerMcpCapable(gen.provider)
}

function handlePersonaChange() {
  gen.provider = personaProvider(gen.persona)
  syncProviderMcpCapable()
}

function openGenerateModal() {
  syncProviderMcpCapable()
  genModal.value = true
  void loadBlueprintHistory()
  void loadGenerationHistory()
}

async function loadCatalogs() {
  try {
    const [pRes, prRes] = await Promise.all([apiConductor.personas(), apiConductor.providers()])
    personas.value = pRes
    providers.value = prRes
    syncProviderMcpCapable()
  }
  catch {}
}

async function loadBlueprintHistory() {
  historyLoading.value = true
  try {
    blueprintHistory.value = await apiConductor.blueprintHistory(projectId.value)
    if (gen.baseBlueprintTerminalId && !blueprintHistory.value.some(item => item.terminal_id === gen.baseBlueprintTerminalId))
      gen.baseBlueprintTerminalId = ''
  }
  catch {
    blueprintHistory.value = []
  }
  finally {
    historyLoading.value = false
  }
}

async function loadGenerationHistory() {
  requestHistoryLoading.value = true
  try {
    generationHistory.value = await apiConductor.generationHistory(projectId.value)
  }
  catch {
    generationHistory.value = []
  }
  finally {
    requestHistoryLoading.value = false
  }
}

// Map terminal_id → completed blueprint (no error)
const blueprintByTerminalId = computed(() => {
  const map = new Map<string, BlueprintResult>()
  blueprintHistory.value.forEach((b) => {
    if (!b.error)
      map.set(b.terminal_id, b)
  })
  return map
})

function loadGenerationRequestIntoForm(req: WorkGraphGenerationRequest) {
  gen.description = req.description
  gen.constraints = req.constraints
  gen.persona = req.persona
  gen.provider = req.provider
  gen.mcpCapable = req.mcp_capable
  gen.baseBlueprintTerminalId = req.base_blueprint_terminal_id || ''
  genTab.value = 'new'
}

async function deleteGenerationRequest(id: number) {
  try {
    await apiConductor.deleteGenerationRequest(id)
    generationHistory.value = generationHistory.value.filter(r => r.id !== id)
    faToast.success(text('Record deleted.', '记录已删除。'))
  }
  catch {
    faToast.error(text('Failed to delete record.', '删除失败，请重试。'))
  }
}

function openBlueprintFromHistory(terminalId: string) {
  const bp = blueprintByTerminalId.value.get(terminalId)
  if (!bp) {
    faToast.error(text('Blueprint not available.', '蓝图不可用。'))
    return
  }
  genTerminalId.value = terminalId
  blueprint.value = bp
  selectedSlugs.value = new Set(bp.items.map((i: BlueprintItem) => i.id))
  reviewModal.value = true
}

const canGenerate = computed(() => Boolean(gen.description.trim() || gen.baseBlueprintTerminalId))

async function startGenerate() {
  if (!canGenerate.value) {
    faToast.warning(text('Project description is required.', '请填写项目描述。'))
    return
  }
  if (!projectStore.currentRootDirectory) {
    faToast.warning(text(
      'No root directory set — blueprint detection via terminal output only.',
      '未设置项目根目录，将通过终端输出检测蓝图。',
    ))
  }
  generating.value = true
  try {
    const result = await apiConductor.generateWorkGraph({
      project_id: projectId.value,
      description: gen.description.trim() || text(
        'Revise the selected previous blueprint into a complete current work graph.',
        '基于选择的历史工作图谱，重新整理成一份完整的当前工作图谱。',
      ),
      constraints: gen.constraints,
      persona: gen.persona,
      provider: gen.provider,
      mcp_capable: gen.mcpCapable,
      base_blueprint_terminal_id: gen.baseBlueprintTerminalId || null,
      language: language.value,
      root_directory: projectStore.currentRootDirectory || null,
    })
    genTerminalId.value = result.terminal_id
    genModal.value = false
    void loadGenerationHistory()
    faToast.success(text(
      'Generation started — we\'ll notify you when the blueprint is ready.',
      '生成任务已启动，蓝图就绪后会自动提醒。',
    ))
    startBgPoll()
  }
  catch {
    faToast.error(text('Generation task failed to start.', '任务提交失败，请重试。'))
  }
  finally {
    generating.value = false
  }
}

async function importSelected() {
  if (!genTerminalId.value || selectedSlugs.value.size === 0)
    return
  importing.value = true
  try {
    await apiConductor.importBlueprint(genTerminalId.value, [...selectedSlugs.value])
    reviewModal.value = false
    genModal.value = false
    blueprint.value = null
    gen.description = ''
    gen.constraints = ''
    gen.baseBlueprintTerminalId = ''
    faToast.success(text('Work items imported.', '工作项已导入。'))
    await Promise.all([loadGraph(), loadBlueprintHistory()])
  }
  finally {
    importing.value = false
  }
}

const quickImportingId = ref<string>('')

async function quickImportAll(terminalId: string) {
  quickImportingId.value = terminalId
  try {
    const items = await apiConductor.importBlueprint(terminalId)
    faToast.success(text(`${items.length} work items imported.`, `已导入 ${items.length} 个工作项。`))
    await Promise.all([loadGraph(), loadBlueprintHistory()])
    genModal.value = false
  }
  catch {
    faToast.error(text('Import failed, please retry.', '导入失败，请重试。'))
  }
  finally {
    quickImportingId.value = ''
  }
}
// ────────────────────────────────────────────────────────────────────

const edgeForm = reactive({
  from_id: '',
  to_id: '',
  type: 'blocks',
  note: '',
})

const createForm = reactive({
  title: '',
  type: 'feature',
  priority: 3,
  complexity: 3,
  description: '',
  owner_terminal_id: '',
  acceptance_criteria: '',
  proof_requirements: '',
  files_of_interest: '',
})

const editForm = reactive({
  title: '',
  description: '',
  priority: 3,
  complexity: 3,
  owner_terminal_id: '',
  acceptance_criteria: '',
  proof_requirements: '',
  files_of_interest: '',
})

const items = computed(() => {
  const all = graph.value?.work_items ?? []
  return statusFilter.value ? all.filter(item => item.status === statusFilter.value) : all
})

const selectedItem = computed(() => graph.value?.work_items.find(item => item.id === selectedItemId.value))

const edgesForSelected = computed(() => {
  if (!selectedItemId.value)
    return []
  return (graph.value?.edges ?? []).filter(edge => edge.from_id === selectedItemId.value || edge.to_id === selectedItemId.value)
})

const criticalSet = computed(() => new Set(graph.value?.critical_path ?? []))

const terminals = computed(() => (state.value?.sessions ?? []).flatMap(s => s.terminals))

onMounted(async () => {
  await Promise.all([loadGraph(), loadState(), loadCatalogs(), loadBlueprintHistory(), loadGenerationHistory()])
})

onBeforeUnmount(() => {
  stopBgPoll()
})

watch(() => projectStore.currentProject, () => {
  selectedItemId.value = ''
  void loadGraph()
  void loadBlueprintHistory()
  void loadGenerationHistory()
})

watch(selectedItemId, () => {
  editMode.value = false
})

watch(genModal, (v) => {
  if (!v) {
    gen.description = ''
    gen.constraints = ''
    gen.baseBlueprintTerminalId = ''
    genTab.value = 'new'
    showAdvanced.value = false
  }
})

async function loadGraph() {
  loading.value = true
  try {
    const result = await apiConductor.workGraph(projectId.value)
    graph.value = {
      project_id: result?.project_id || projectId.value,
      work_items: Array.isArray(result?.work_items) ? result.work_items : [],
      edges: Array.isArray(result?.edges) ? result.edges : [],
      critical_path: Array.isArray(result?.critical_path) ? result.critical_path : [],
      scope_conflicts: Array.isArray(result?.scope_conflicts) ? result.scope_conflicts : [],
    }
    if (!selectedItemId.value && graph.value.work_items.length) {
      await selectItem(graph.value.work_items[0])
    }
  }
  finally {
    loading.value = false
  }
}

async function loadState() {
  try {
    state.value = await apiConductor.dashboardState(true)
  }
  catch {
    state.value = null
  }
}

async function selectItem(item: WorkItem) {
  selectedItemId.value = item.id
  edgeForm.from_id = item.id
  edgeForm.to_id = edgeForm.to_id || ''
  await loadProofWindows(item.id)
}

async function loadProofWindows(itemId: string) {
  proofWindows.value = await apiConductor.proofWindows(itemId)
}

async function setStatus(item: WorkItem, status: string) {
  await apiConductor.updateWorkItem(item.id, { status })
  faToast.success(text('Status updated.', '状态已更新。'))
  await loadGraph()
}

async function createWorkItem() {
  if (!createForm.title.trim()) {
    faToast.warning(text('Title is required.', '请填写工作项标题。'))
    return
  }
  createLoading.value = true
  try {
    await apiConductor.createWorkItem({
      project_id: projectId.value,
      title: createForm.title.trim(),
      type: createForm.type,
      priority: createForm.priority,
      complexity: createForm.complexity,
      description: createForm.description.trim() || undefined,
      owner_terminal_id: createForm.owner_terminal_id || null,
      acceptance_criteria: createForm.acceptance_criteria.split('\n').map(s => s.trim()).filter(Boolean),
      proof_requirements: createForm.proof_requirements.split('\n').map(s => s.trim()).filter(Boolean),
      files_of_interest: createForm.files_of_interest.split('\n').map(s => s.trim()).filter(Boolean),
    })
    faToast.success(text('Work item created.', '工作项已创建。'))
    createForm.title = ''
    createForm.description = ''
    createForm.owner_terminal_id = ''
    createForm.acceptance_criteria = ''
    createForm.proof_requirements = ''
    createForm.files_of_interest = ''
    showCreateForm.value = false
    await loadGraph()
  }
  finally {
    createLoading.value = false
  }
}

async function deleteItem(item: WorkItem) {
  deleteLoading.value = true
  try {
    await apiConductor.deleteWorkItem(item.id)
    faToast.success(text('Work item deleted.', '工作项已删除。'))
    selectedItemId.value = ''
    proofWindows.value = []
    await loadGraph()
  }
  finally {
    deleteLoading.value = false
  }
}

function startEdit() {
  if (!selectedItem.value)
    return
  const item = selectedItem.value
  editForm.title = item.title
  editForm.description = item.description
  editForm.priority = item.priority
  editForm.complexity = item.complexity
  editForm.owner_terminal_id = item.owner_terminal_id || ''
  editForm.acceptance_criteria = item.acceptance_criteria.join('\n')
  editForm.proof_requirements = (item.proof_requirements ?? []).join('\n')
  editForm.files_of_interest = item.files_of_interest.join('\n')
  editMode.value = true
}

function cancelEdit() {
  editMode.value = false
}

async function saveEdit() {
  if (!selectedItem.value)
    return
  if (!editForm.title.trim()) {
    faToast.warning(text('Title is required.', '请填写工作项标题。'))
    return
  }
  editLoading.value = true
  try {
    const criteria = editForm.acceptance_criteria.split('\n').map(s => s.trim()).filter(Boolean)
    const proofRequirements = editForm.proof_requirements.split('\n').map(s => s.trim()).filter(Boolean)
    const files = editForm.files_of_interest.split('\n').map(s => s.trim()).filter(Boolean)
    await apiConductor.updateWorkItem(selectedItem.value.id, {
      title: editForm.title.trim(),
      description: editForm.description.trim(),
      priority: editForm.priority,
      complexity: editForm.complexity,
      owner_terminal_id: editForm.owner_terminal_id || null,
      acceptance_criteria: criteria,
      proof_requirements: proofRequirements.length ? proofRequirements : null,
      files_of_interest: files,
    })
    faToast.success(text('Work item updated.', '工作项已更新。'))
    editMode.value = false
    await loadGraph()
  }
  finally {
    editLoading.value = false
  }
}

async function createEdge() {
  if (!edgeForm.from_id || !edgeForm.to_id) {
    faToast.warning(text('Select both edge endpoints.', '请选择依赖关系的两端。'))
    return
  }
  edgeLoading.value = true
  try {
    await apiConductor.createWorkEdge(edgeForm.from_id, {
      from_id: edgeForm.from_id,
      to_id: edgeForm.to_id,
      type: edgeForm.type,
      created_by: 'dashboard',
      note: edgeForm.note || null,
    })
    edgeForm.note = ''
    faToast.success(text('Dependency added.', '依赖关系已添加。'))
    await loadGraph()
  }
  finally {
    edgeLoading.value = false
  }
}

async function deleteEdge(edge: WorkEdge) {
  await apiConductor.deleteWorkEdge(edge.id)
  faToast.success(text('Dependency removed.', '依赖关系已移除。'))
  await loadGraph()
}

function launchItem(item: WorkItem) {
  const lines: string[] = [item.title]
  if (item.description)
    lines.push('', item.description)
  if (item.acceptance_criteria.length) {
    lines.push('', text('Acceptance criteria:', '验收标准：'))
    item.acceptance_criteria.forEach(c => lines.push(`- ${c}`))
  }
  if (item.proof_requirements?.length) {
    lines.push('', text('Proof required:', '所需证据：'))
    item.proof_requirements.forEach(p => lines.push(`- ${p}`))
  }
  router.push({
    path: '/tasks',
    query: { workItemId: item.id, title: item.title, objective: lines.join('\n') },
  })
}

function itemTitle(id: string) {
  return graph.value?.work_items.find(item => item.id === id)?.title || id
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    ready: text('Ready', '就绪'),
    blocked: text('Blocked', '已阻塞'),
    in_progress: text('In progress', '进行中'),
    needs_verification: text('Needs verify', '待验证'),
    done: text('Done', '已完成'),
    cancelled: text('Cancelled', '已取消'),
  }
  return labels[status] ?? status
}

function typeLabel(type: string) {
  const labels: Record<string, string> = {
    feature: text('Feature', '新功能'),
    bugfix: text('Bug fix', '缺陷修复'),
    refactor: text('Refactor', '代码重构'),
    test: text('Test', '测试验证'),
    documentation: text('Docs', '文档整理'),
    review: text('Review', '代码审查'),
    investigation: text('Research', '调研分析'),
  }
  return labels[type] ?? type
}

function edgeTypeLabel(type: string) {
  const labels: Record<string, string> = {
    blocks: text('Blocks', '阻塞'),
    enables: text('Enables', '使能'),
    conflicts_with: text('Conflicts', '冲突'),
    validates: text('Validates', '验证'),
    collaborates_on: text('Collab', '协作'),
  }
  return labels[type] ?? type
}

function statusBadgeClass(status: string) {
  const map: Record<string, string> = {
    ready: 'bg-slate-500/10 text-slate-600',
    blocked: 'bg-red-500/10 text-red-600',
    in_progress: 'bg-blue-500/10 text-blue-700',
    needs_verification: 'bg-amber-500/10 text-amber-700',
    done: 'bg-emerald-500/10 text-emerald-700',
    cancelled: 'bg-muted text-muted-foreground',
  }
  return map[status] ?? 'bg-muted text-muted-foreground'
}

function pretty(value: unknown) {
  return JSON.stringify(value, null, 2)
}
</script>

<template>
  <div class="mx-auto max-w-7xl space-y-6 p-6">
    <!-- Header -->
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <div class="text-sm text-muted-foreground">
          {{ text('Causal work tracking, proof collection, dependency risks', '因果工作跟踪、证据收集与依赖风险管理') }}
        </div>
        <h1 class="mt-1 text-2xl font-semibold">
          {{ text('Work Graph', '工作图谱') }}
        </h1>
      </div>
      <div class="flex flex-wrap gap-2">
        <select v-model="statusFilter" class="border rounded-md bg-background px-3 py-2 text-sm">
          <option value="">
            {{ text('All statuses', '全部状态') }}
          </option>
          <option value="ready">
            {{ text('Ready', '就绪') }}
          </option>
          <option value="blocked">
            {{ text('Blocked', '已阻塞') }}
          </option>
          <option value="in_progress">
            {{ text('In progress', '进行中') }}
          </option>
          <option value="needs_verification">
            {{ text('Needs verify', '待验证') }}
          </option>
          <option value="done">
            {{ text('Done', '已完成') }}
          </option>
          <option value="cancelled">
            {{ text('Cancelled', '已取消') }}
          </option>
        </select>
        <FaButton variant="outline" @click="openGenerateModal">
          <FaIcon name="i-lucide:wand-sparkles" class="mr-2" />
          {{ text('Auto-generate', '智能生成') }}
        </FaButton>
        <FaButton variant="outline" :loading="loading" @click="loadGraph">
          <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
          {{ text('Refresh', '刷新') }}
        </FaButton>
      </div>
    </div>

    <!-- Generate config modal -->
    <div v-if="genModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" @click.self="genModal = false">
      <div class="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border bg-background shadow-2xl">
        <!-- Modal header -->
        <div class="flex items-center justify-between border-b px-6 py-4">
          <div class="flex items-center gap-2">
            <FaIcon name="i-lucide:wand-sparkles" class="text-primary" />
            <h2 class="text-lg font-semibold">{{ text('Auto-generate Work Graph', '智能生成工作图谱') }}</h2>
          </div>
          <button
            class="rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
            @click="genModal = false"
          >
            <FaIcon name="i-lucide:x" />
          </button>
        </div>

        <!-- Tabs -->
        <div class="flex border-b px-6">
          <button
            class="flex items-center gap-1.5 border-b-2 px-4 py-3 text-sm font-medium transition"
            :class="genTab === 'new' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
            @click="genTab = 'new'"
          >
            <FaIcon name="i-lucide:plus" class="inline" />
            {{ text('New', '新建') }}
          </button>
          <button
            class="flex items-center gap-1.5 border-b-2 px-4 py-3 text-sm font-medium transition"
            :class="genTab === 'history' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
            @click="genTab = 'history'; loadGenerationHistory(); loadBlueprintHistory()"
          >
            <FaIcon name="i-lucide:clock" class="inline" />
            {{ text('History', '历史记录') }}
            <span v-if="generationHistory.length" class="rounded-full bg-muted px-1.5 py-0.5 text-xs leading-none">
              {{ generationHistory.length }}
            </span>
          </button>
        </div>

        <!-- ── NEW TAB ───────────────────────────────────────────────── -->
        <div v-if="genTab === 'new'" class="flex-1 space-y-5 overflow-auto p-6">
          <!-- Project description -->
          <div>
            <div class="mb-2 flex items-center justify-between">
              <label class="text-sm font-medium">{{ text('Project description', '项目描述') }}</label>
              <span class="text-xs text-muted-foreground">{{ text('Quick templates →', '快速模板 →') }}</span>
            </div>
            <!-- Template grid -->
            <div class="mb-3 grid grid-cols-4 gap-1.5">
              <button
                v-for="tmpl in descriptionTemplates"
                :key="tmpl.label"
                class="flex flex-col items-center gap-1 rounded-lg border px-1 py-2 text-center text-xs transition hover:border-primary/60 hover:bg-primary/5 hover:text-primary"
                :class="gen.description === tmpl.value ? 'border-primary bg-primary/5 text-primary' : ''"
                @click="applyDescriptionTemplate(tmpl)"
              >
                <FaIcon :name="tmpl.icon" class="size-3.5" />
                <span>{{ text(tmpl.label, tmpl.labelZh) }}</span>
              </button>
            </div>
            <textarea
              v-model="gen.description"
              rows="7"
              class="w-full rounded-md border bg-muted/20 p-3 text-sm outline-none focus:ring-2 focus:ring-primary/30"
              :placeholder="text('Describe your project… or pick a template above', '描述你的项目，或从上方点击模板快速填入…')"
            />
          </div>

          <!-- Constraints -->
          <div>
            <div class="mb-2 flex items-center justify-between">
              <label class="text-sm font-medium">{{ text('Additional constraints', '补充约束') }}</label>
              <span class="text-xs text-muted-foreground">{{ text('Click chips to append →', '点击添加 →') }}</span>
            </div>
            <div class="mb-3 flex flex-wrap gap-1.5">
              <button
                v-for="chip in constraintChips"
                :key="chip.label"
                class="rounded-full border px-3 py-1 text-xs text-muted-foreground transition hover:border-primary hover:bg-primary/5 hover:text-primary"
                @click="appendConstraint(chip.value)"
              >
                + {{ chip.label }}
              </button>
            </div>
            <textarea
              v-model="gen.constraints"
              rows="3"
              class="w-full rounded-md border bg-muted/20 p-3 text-sm outline-none focus:ring-2 focus:ring-primary/30"
              :placeholder="text('Additional constraints (optional) — selected chips are appended here', '补充约束（可选），点击上方标签会追加到此处')"
            />
          </div>

          <!-- Advanced options (collapsible) -->
          <div class="rounded-lg border">
            <button
              class="flex w-full items-center justify-between px-4 py-3 text-sm font-medium transition hover:bg-muted/30"
              @click="showAdvanced = !showAdvanced"
            >
              <span class="flex items-center gap-1.5 text-muted-foreground">
                <FaIcon name="i-lucide:settings-2" class="size-3.5" />
                {{ text('Advanced options', '高级选项') }}
              </span>
              <FaIcon :name="showAdvanced ? 'i-lucide:chevron-up' : 'i-lucide:chevron-down'" class="size-3.5 text-muted-foreground" />
            </button>
            <div v-if="showAdvanced" class="space-y-3 border-t px-4 pb-4 pt-3">
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="mb-1 block text-xs text-muted-foreground">Persona</label>
                  <select v-model="gen.persona" class="w-full rounded-md border bg-background px-3 py-2 text-sm" @change="handlePersonaChange">
                    <option v-for="p in personas" :key="p.name" :value="p.name">{{ p.name }}</option>
                  </select>
                </div>
                <div>
                  <label class="mb-1 block text-xs text-muted-foreground">Provider</label>
                  <select
                    v-model="gen.provider"
                    class="w-full rounded-md border bg-background px-3 py-2 text-sm"
                    @change="syncProviderMcpCapable"
                  >
                    <option v-for="p in providers" :key="p.key" :value="p.key">{{ p.label }}</option>
                  </select>
                </div>
              </div>
              <div>
                <label class="mb-1 block text-xs text-muted-foreground">{{ text('History baseline (optional)', '历史基线（可选，基于已有蓝图修订）') }}</label>
                <select v-model="gen.baseBlueprintTerminalId" class="w-full rounded-md border bg-background px-3 py-2 text-sm">
                  <option value="">{{ text('Start from scratch', '从新需求开始') }}</option>
                  <option v-for="item in blueprintHistory" :key="item.terminal_id" :value="item.terminal_id">
                    {{ item.meta?.title || item.terminal_id }} · {{ item.items.length }} {{ text('items', '项') }} · {{ item.created_at?.slice(0, 10) }}
                  </option>
                </select>
              </div>
              <label class="flex cursor-pointer items-center gap-2 text-sm select-none">
                <input v-model="gen.mcpCapable" type="checkbox" class="rounded" />
                <span>{{ text('Provider supports MCP tools', 'Provider 支持 MCP 工具（submit_blueprint）') }}</span>
                <span class="ml-auto rounded bg-muted px-1.5 py-0.5 text-xs">{{ gen.mcpCapable ? 'MCP' : 'stdout' }}</span>
              </label>
            </div>
          </div>
        </div>

        <!-- ── HISTORY TAB ────────────────────────────────────────────── -->
        <div v-else class="flex-1 overflow-auto p-6">
          <div class="mb-4 flex items-center justify-between">
            <p class="text-sm text-muted-foreground">
              {{ text('Click "Load" to restore a past session into the form. Click "Review" if the blueprint is ready to import.', '点击「加载」将历史输入恢复到表单；点击「查看蓝图」可审核并导入工作项。') }}
            </p>
            <FaButton variant="outline" size="sm" :loading="requestHistoryLoading || historyLoading" @click="loadGenerationHistory(); loadBlueprintHistory()">
              <FaIcon name="i-lucide:refresh-cw" class="mr-1" />
              {{ text('Refresh', '刷新') }}
            </FaButton>
          </div>

          <div v-if="generationHistory.length === 0" class="flex flex-col items-center py-12 text-muted-foreground">
            <FaIcon name="i-lucide:inbox" class="mb-3 size-10 opacity-25" />
            <div class="text-sm">{{ text('No generation history yet.', '暂无历史记录，先去新建一个吧。') }}</div>
          </div>

          <div class="space-y-3">
            <div
              v-for="req in generationHistory"
              :key="req.id"
              class="rounded-lg border bg-background p-4 transition hover:border-primary/40"
            >
              <div class="mb-3 flex items-start justify-between gap-3">
                <div class="min-w-0 flex-1">
                  <p class="line-clamp-2 text-sm font-medium">{{ req.description || text('(no description)', '（无描述）') }}</p>
                  <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                    <span class="rounded bg-muted px-1.5 py-0.5">{{ req.persona }}</span>
                    <span class="rounded bg-muted px-1.5 py-0.5">{{ req.provider }}</span>
                    <span>{{ req.created_at?.slice(0, 16).replace('T', ' ') }}</span>
                    <span v-if="req.mcp_capable" class="rounded bg-primary/10 px-1.5 py-0.5 text-primary">MCP</span>
                  </div>
                  <p v-if="req.constraints" class="mt-1.5 line-clamp-1 text-xs text-muted-foreground">
                    {{ text('Constraints:', '约束：') }} {{ req.constraints }}
                  </p>
                </div>
                <!-- Blueprint status badge -->
                <div class="shrink-0">
                  <span v-if="blueprintByTerminalId.has(req.terminal_id)" class="flex items-center gap-1 rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-600">
                    <FaIcon name="i-lucide:check-circle" class="size-3" />
                    {{ blueprintByTerminalId.get(req.terminal_id)?.items.length }} {{ text('items ready', '项已就绪') }}
                  </span>
                  <span v-else class="flex items-center gap-1 rounded-full bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-600">
                    <FaIcon name="i-lucide:loader-circle" class="size-3" />
                    {{ text('Pending', '规划中') }}
                  </span>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <FaButton variant="outline" size="sm" @click="loadGenerationRequestIntoForm(req)">
                  <FaIcon name="i-lucide:upload" class="mr-1" />
                  {{ text('Load into form', '加载到表单') }}
                </FaButton>
                <FaButton
                  v-if="blueprintByTerminalId.has(req.terminal_id)"
                  size="sm"
                  @click="openBlueprintFromHistory(req.terminal_id)"
                >
                  <FaIcon name="i-lucide:eye" class="mr-1" />
                  {{ text('Review & Import', '查看蓝图') }}
                </FaButton>
                <FaButton
                  v-if="blueprintByTerminalId.has(req.terminal_id)"
                  variant="outline"
                  size="sm"
                  :loading="quickImportingId === req.terminal_id"
                  @click="quickImportAll(req.terminal_id)"
                >
                  <FaIcon name="i-lucide:zap" class="mr-1" />
                  {{ text('Import All', '一键导入') }}
                </FaButton>
                <FaButton variant="outline" size="sm" class="ml-auto text-destructive hover:border-destructive hover:text-destructive" @click="deleteGenerationRequest(req.id)">
                  <FaIcon name="i-lucide:trash-2" class="size-3.5" />
                </FaButton>
              </div>
            </div>
          </div>
        </div>

        <!-- Modal footer (new tab only, hidden while watching) -->
        <div v-if="genTab === 'new'" class="flex items-center justify-end gap-2 border-t px-6 py-4">
          <FaButton variant="outline" @click="genModal = false">{{ text('Cancel', '取消') }}</FaButton>
          <FaButton :disabled="!canGenerate || generating" :loading="generating" @click="startGenerate">
            <FaIcon name="i-lucide:wand-sparkles" class="mr-2" />
            {{ text('Start generation', '开始生成') }}
          </FaButton>
        </div>
      </div>
    </div>

    <!-- Blueprint review modal -->
    <div v-if="reviewModal && blueprint" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="reviewModal = false">
      <div class="flex h-[80vh] w-full max-w-2xl flex-col rounded-xl border bg-background shadow-xl">
        <div class="flex items-center justify-between border-b px-5 py-4">
          <div>
            <h2 class="font-semibold">{{ text('Review Blueprint', '审核工作图谱') }}</h2>
            <div class="text-xs text-muted-foreground">
              {{ blueprint.items.length }} {{ text('items generated', '个工作项已生成') }}
              <span v-if="blueprint.meta.title" class="ml-2">· {{ blueprint.meta.title }}</span>
            </div>
          </div>
          <FaButton variant="outline" size="sm" @click="reviewModal = false">{{ text('Close', '关闭') }}</FaButton>
        </div>
        <div class="flex-1 overflow-auto p-4 space-y-2">
          <label
            v-for="item in blueprint.items"
            :key="item.id"
            class="flex cursor-pointer gap-3 rounded-lg border p-3 transition hover:bg-muted/30"
            :class="selectedSlugs.has(item.id) ? 'border-primary/50 bg-primary/5' : ''"
          >
            <input
              type="checkbox"
              :checked="selectedSlugs.has(item.id)"
              class="mt-0.5 shrink-0"
              @change="selectedSlugs.has(item.id) ? selectedSlugs.delete(item.id) : selectedSlugs.add(item.id)"
            />
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-medium text-sm">{{ item.title }}</span>
                <span class="rounded bg-muted px-1.5 py-0.5 text-xs">{{ item.category }}</span>
                <span class="text-xs text-muted-foreground">P{{ item.priority }}</span>
                <span v-if="item.effort_hours" class="text-xs text-muted-foreground">{{ item.effort_hours }}h</span>
              </div>
              <p class="mt-1 line-clamp-2 text-xs text-muted-foreground">{{ item.description }}</p>
              <div v-if="item.depends_on.length" class="mt-1 text-xs text-muted-foreground">
                → {{ text('depends on', '依赖') }}: {{ item.depends_on.join(', ') }}
              </div>
            </div>
          </label>
        </div>
        <div class="flex items-center justify-between border-t px-5 py-3">
          <span class="text-sm text-muted-foreground">
            {{ selectedSlugs.size }} / {{ blueprint.items.length }} {{ text('selected', '已选') }}
          </span>
          <div class="flex gap-2">
            <FaButton variant="outline" size="sm" @click="selectedSlugs = new Set(blueprint.items.map(i => i.id))">
              {{ text('Select all', '全选') }}
            </FaButton>
            <FaButton :loading="importing" :disabled="selectedSlugs.size === 0" @click="importSelected">
              <FaIcon name="i-lucide:download" class="mr-2" />
              {{ text('Import selected', '导入所选') }}
            </FaButton>
          </div>
        </div>
      </div>
    </div>

    <!-- Stats -->
    <div class="grid gap-4 md:grid-cols-3">
      <div class="rounded-lg border bg-background p-4">
        <div class="text-xs text-muted-foreground">
          {{ text('Open Items', '进行中工作项') }}
        </div>
        <div class="mt-2 text-2xl font-semibold">
          {{ (graph?.work_items ?? []).filter(item => !['done', 'cancelled'].includes(item.status)).length }}
        </div>
      </div>
      <div class="rounded-lg border bg-background p-4">
        <div class="text-xs text-muted-foreground">
          {{ text('Critical Path', '关键路径') }}
        </div>
        <div class="mt-2 text-2xl font-semibold text-amber-600">
          {{ graph?.critical_path.length ?? 0 }}
        </div>
      </div>
      <div class="rounded-lg border bg-background p-4">
        <div class="text-xs text-muted-foreground">
          {{ text('Scope Conflicts', '范围冲突') }}
        </div>
        <div class="mt-2 text-2xl font-semibold" :class="(graph?.scope_conflicts.length ?? 0) > 0 ? 'text-red-600' : ''">
          {{ graph?.scope_conflicts.length ?? 0 }}
        </div>
      </div>
    </div>

    <div class="grid gap-4 xl:grid-cols-[1fr_380px]">
      <!-- Work items list -->
      <section class="border rounded-lg bg-background p-4">
        <div class="mb-3 flex items-center justify-between gap-3">
          <h2 class="text-lg font-semibold">
            {{ text('Work Items', '工作项') }}
          </h2>
          <div class="flex items-center gap-2">
            <span class="text-sm text-muted-foreground">{{ items.length }} {{ text('shown', '项') }}</span>
            <FaButton size="sm" @click="showCreateForm = !showCreateForm">
              <FaIcon name="i-lucide:plus" class="mr-1" />
              {{ showCreateForm ? text('Cancel', '取消') : text('New item', '新建工作项') }}
            </FaButton>
          </div>
        </div>

        <!-- Create form (inline) -->
        <div v-if="showCreateForm" class="mb-4 rounded-lg border border-primary/30 bg-primary/5 p-4">
          <h3 class="mb-3 font-medium">
            {{ text('Create Work Item', '新建工作项') }}
          </h3>
          <div class="grid gap-3">
            <FaInput v-model="createForm.title" :placeholder="text('Title (required)', '标题（必填）')" />
            <div class="grid gap-3 sm:grid-cols-2">
              <select v-model="createForm.type" class="border rounded-md bg-background px-3 py-2 text-sm">
                <option value="feature">
                  {{ text('Feature', '新功能') }}
                </option>
                <option value="bugfix">
                  {{ text('Bug fix', '缺陷修复') }}
                </option>
                <option value="refactor">
                  {{ text('Refactor', '代码重构') }}
                </option>
                <option value="test">
                  {{ text('Test', '测试验证') }}
                </option>
                <option value="documentation">
                  {{ text('Docs', '文档整理') }}
                </option>
                <option value="review">
                  {{ text('Review', '代码审查') }}
                </option>
                <option value="investigation">
                  {{ text('Research', '调研分析') }}
                </option>
              </select>
              <select v-model="createForm.owner_terminal_id" class="border rounded-md bg-background px-3 py-2 text-sm">
                <option value="">
                  {{ text('No owner terminal', '暂不指定执行终端') }}
                </option>
                <option v-for="terminal in terminals" :key="terminal.id" :value="terminal.id">
                  {{ terminal.window_name }}
                </option>
              </select>
            </div>
            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <div class="mb-1 text-xs text-muted-foreground">
                  {{ text('Priority', '优先级') }} ({{ createForm.priority }}/5)
                </div>
                <input v-model.number="createForm.priority" type="range" min="1" max="5" class="w-full">
              </div>
              <div>
                <div class="mb-1 text-xs text-muted-foreground">
                  {{ text('Complexity', '复杂度') }} ({{ createForm.complexity }}/5)
                </div>
                <input v-model.number="createForm.complexity" type="range" min="1" max="5" class="w-full">
              </div>
            </div>
            <textarea
              v-model="createForm.description"
              rows="2"
              class="w-full border rounded-md bg-background p-3 text-sm outline-none focus:ring-2 focus:ring-primary/30"
              :placeholder="text('Description (optional)', '描述（可选）')"
            />
            <textarea
              v-model="createForm.acceptance_criteria"
              rows="2"
              class="w-full border rounded-md bg-background p-3 text-sm outline-none focus:ring-2 focus:ring-primary/30"
              :placeholder="text('Acceptance criteria — one per line (optional)', '验收标准，每行一条（可选）')"
            />
            <textarea
              v-model="createForm.proof_requirements"
              rows="1"
              class="w-full border rounded-md bg-background p-3 text-sm outline-none focus:ring-2 focus:ring-primary/30"
              :placeholder="text('Proof requirements — one per line, e.g. git_commit (optional)', '所需证据，每行一条，如 git_commit（可选）')"
            />
            <textarea
              v-model="createForm.files_of_interest"
              rows="2"
              class="w-full border rounded-md bg-background p-3 text-sm font-mono outline-none focus:ring-2 focus:ring-primary/30"
              :placeholder="text('Files of interest — one path per line (optional)', '关联文件，每行一个路径（可选）')"
            />
            <FaButton class="w-full" :loading="createLoading" @click="createWorkItem">
              <FaIcon name="i-lucide:plus" class="mr-2" />
              {{ text('Create', '创建') }}
            </FaButton>
          </div>
        </div>

        <!-- Items list -->
        <div class="space-y-3">
          <article
            v-for="item in items"
            :key="item.id"
            class="rounded-lg border p-4 transition hover:border-primary/50"
            :class="[
              selectedItemId === item.id ? 'border-primary bg-primary/5' : '',
              criticalSet.has(item.id) ? 'ring-1 ring-amber-400/60' : '',
            ]"
            @click="selectItem(item)"
          >
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <h3 class="font-semibold">
                    {{ item.title }}
                  </h3>
                  <span v-if="criticalSet.has(item.id)" class="rounded bg-amber-500/10 px-2 py-0.5 text-xs text-amber-700">
                    {{ text('critical', '关键路径') }}
                  </span>
                  <span class="rounded px-2 py-0.5 text-xs" :class="statusBadgeClass(item.status)">
                    {{ statusLabel(item.status) }}
                  </span>
                </div>
                <div class="mt-1 text-xs text-muted-foreground">
                  {{ typeLabel(item.type) }} · {{ text('P', '优先级 ') }}{{ item.priority }} · {{ text('C', '复杂度 ') }}{{ item.complexity }}
                  <span v-if="item.owner_terminal_id"> · {{ item.owner_terminal_id.slice(0, 12) }}…</span>
                </div>
              </div>
              <select
                :value="item.status"
                class="border rounded-md bg-background px-2 py-1 text-xs"
                @click.stop
                @change="setStatus(item, ($event.target as HTMLSelectElement).value)"
              >
                <option value="ready">
                  {{ text('Ready', '就绪') }}
                </option>
                <option value="blocked">
                  {{ text('Blocked', '已阻塞') }}
                </option>
                <option value="in_progress">
                  {{ text('In progress', '进行中') }}
                </option>
                <option value="needs_verification">
                  {{ text('Needs verify', '待验证') }}
                </option>
                <option value="done">
                  {{ text('Done', '已完成') }}
                </option>
                <option value="cancelled">
                  {{ text('Cancelled', '已取消') }}
                </option>
              </select>
            </div>
            <p v-if="item.description" class="mt-2 text-sm text-muted-foreground">
              {{ item.description }}
            </p>
            <div v-if="item.files_of_interest.length" class="mt-2 text-xs text-muted-foreground">
              {{ text('Files', '关联文件') }}: {{ item.files_of_interest.join(', ') }}
            </div>
          </article>
          <div v-if="items.length === 0" class="flex flex-col items-center py-10 text-muted-foreground">
            <FaIcon name="i-lucide:inbox" class="mb-3 size-8 opacity-30" />
            <div class="text-sm">
              {{ text('No work items for this filter.', '当前筛选条件下没有工作项。') }}
            </div>
          </div>
        </div>
      </section>

      <!-- Right sidebar -->
      <aside class="space-y-4">
        <!-- Selected item detail -->
        <section class="border rounded-lg bg-background p-4">
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold">
              {{ text('Selected Item', '选中工作项') }}
            </h2>
            <div v-if="selectedItem" class="flex gap-1">
              <FaButton variant="outline" size="sm" @click="startEdit">
                <FaIcon name="i-lucide:pencil" class="mr-1" />
                {{ text('Edit', '编辑') }}
              </FaButton>
              <FaButton variant="outline" size="sm" @click="launchItem(selectedItem)">
                <FaIcon name="i-lucide:play" class="mr-1 text-emerald-500" />
                {{ text('Launch', '启动') }}
              </FaButton>
              <FaButton variant="outline" size="sm" :loading="deleteLoading" @click="deleteItem(selectedItem)">
                <FaIcon name="i-lucide:trash-2" class="mr-1 text-red-500" />
                {{ text('Delete', '删除') }}
              </FaButton>
            </div>
          </div>

          <!-- Edit form -->
          <div v-if="editMode && selectedItem" class="mt-3 space-y-3">
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">{{ text('Title', '标题') }}</label>
              <FaInput v-model="editForm.title" :placeholder="text('Title', '标题')" />
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">{{ text('Description', '描述') }}</label>
              <textarea
                v-model="editForm.description"
                rows="3"
                class="w-full rounded-md border bg-background p-2 text-sm outline-none focus:ring-2 focus:ring-primary/30"
                :placeholder="text('Description', '描述')"
              />
            </div>
            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <div class="mb-1 text-xs text-muted-foreground">{{ text('Priority', '优先级') }} ({{ editForm.priority }}/5)</div>
                <input v-model.number="editForm.priority" type="range" min="1" max="5" class="w-full">
              </div>
              <div>
                <div class="mb-1 text-xs text-muted-foreground">{{ text('Complexity', '复杂度') }} ({{ editForm.complexity }}/5)</div>
                <input v-model.number="editForm.complexity" type="range" min="1" max="5" class="w-full">
              </div>
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">{{ text('Owner Terminal', '执行终端') }}</label>
              <select v-model="editForm.owner_terminal_id" class="w-full border rounded-md bg-background px-3 py-2 text-sm">
                <option value="">{{ text('No owner terminal', '暂不指定') }}</option>
                <option v-for="terminal in terminals" :key="terminal.id" :value="terminal.id">
                  {{ terminal.window_name }}
                </option>
              </select>
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">{{ text('Acceptance Criteria (one per line)', '验收标准（每行一条）') }}</label>
              <textarea
                v-model="editForm.acceptance_criteria"
                rows="3"
                class="w-full rounded-md border bg-background p-2 text-sm outline-none focus:ring-2 focus:ring-primary/30"
                :placeholder="text('One criterion per line', '每行一条')"
              />
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">{{ text('Proof Requirements (one per line)', '所需证据（每行一条）') }}</label>
              <textarea
                v-model="editForm.proof_requirements"
                rows="2"
                class="w-full rounded-md border bg-background p-2 text-sm outline-none focus:ring-2 focus:ring-primary/30"
                :placeholder="text('e.g. git_commit, test_pass', '例如 git_commit、test_pass')"
              />
            </div>
            <div>
              <label class="mb-1 block text-xs text-muted-foreground">{{ text('Files of Interest (one per line)', '关联文件（每行一个）') }}</label>
              <textarea
                v-model="editForm.files_of_interest"
                rows="2"
                class="w-full rounded-md border bg-background p-2 text-sm outline-none focus:ring-2 focus:ring-primary/30"
                :placeholder="text('One file path per line', '每行一个路径')"
              />
            </div>
            <div class="flex gap-2">
              <FaButton class="flex-1" :loading="editLoading" @click="saveEdit">
                <FaIcon name="i-lucide:check" class="mr-1" />
                {{ text('Save', '保存') }}
              </FaButton>
              <FaButton variant="outline" class="flex-1" @click="cancelEdit">
                {{ text('Cancel', '取消') }}
              </FaButton>
            </div>
          </div>

          <!-- Read-only view -->
          <div v-else-if="selectedItem" class="mt-3 space-y-3 text-sm">
            <div>
              <div class="text-xs text-muted-foreground">
                {{ text('Title', '标题') }}
              </div>
              <div class="font-medium">
                {{ selectedItem.title }}
              </div>
            </div>
            <div class="grid gap-3 text-xs sm:grid-cols-2">
              <div>
                <div class="text-muted-foreground">
                  {{ text('Type', '类型') }}
                </div>
                <div>{{ typeLabel(selectedItem.type) }}</div>
              </div>
              <div>
                <div class="text-muted-foreground">
                  {{ text('Status', '状态') }}
                </div>
                <div>{{ statusLabel(selectedItem.status) }}</div>
              </div>
              <div>
                <div class="text-muted-foreground">
                  {{ text('Priority', '优先级') }}
                </div>
                <div>{{ selectedItem.priority }}</div>
              </div>
              <div>
                <div class="text-muted-foreground">
                  {{ text('Complexity', '复杂度') }}
                </div>
                <div>{{ selectedItem.complexity }}</div>
              </div>
            </div>
            <div v-if="selectedItem.acceptance_criteria.length">
              <div class="text-xs text-muted-foreground">
                {{ text('Acceptance Criteria', '验收标准') }}
              </div>
              <ul class="mt-1 list-disc pl-4 text-xs">
                <li v-for="criterion in selectedItem.acceptance_criteria" :key="criterion">
                  {{ criterion }}
                </li>
              </ul>
            </div>
            <div v-if="selectedItem.proof_requirements?.length">
              <div class="text-xs text-muted-foreground">
                {{ text('Proof Required', '所需证据') }}
              </div>
              <div class="mt-1 text-xs">
                {{ selectedItem.proof_requirements.join(', ') }}
              </div>
            </div>
          </div>

          <div v-else class="mt-3 py-4 text-center text-sm text-muted-foreground">
            {{ text('Click a work item to inspect it.', '点击工作项查看详情。') }}
          </div>
        </section>

        <!-- Proof windows -->
        <section class="border rounded-lg bg-background p-4">
          <h2 class="text-base font-semibold">
            {{ text('Proof Windows', '证据窗口') }}
          </h2>
          <div class="mt-3 space-y-2">
            <div v-for="pw in proofWindows" :key="pw.id" class="rounded-md border p-3 text-sm">
              <div class="flex items-center justify-between gap-3">
                <span class="font-medium">#{{ pw.id }}</span>
                <span class="rounded bg-muted px-2 py-0.5 text-xs">{{ pw.status }}</span>
              </div>
              <div class="mt-1 text-xs text-muted-foreground">
                {{ text('Expires', '到期时间') }} {{ pw.expires_at }}
              </div>
              <pre class="mt-2 max-h-32 overflow-auto rounded bg-muted/40 p-2 text-xs">{{ pretty(pw.proofs_collected || []) }}</pre>
            </div>
            <div v-if="proofWindows.length === 0" class="py-4 text-center text-sm text-muted-foreground">
              {{ text('No proof windows.', '暂无证据窗口。') }}
            </div>
          </div>
        </section>

        <!-- Dependency edges -->
        <section class="border rounded-lg bg-background p-4">
          <h2 class="text-base font-semibold">
            {{ text('Dependencies', '依赖关系') }}
          </h2>
          <!-- Add edge form -->
          <div class="mt-3 space-y-2">
            <select v-model="edgeForm.from_id" class="w-full border rounded-md bg-background px-3 py-2 text-sm">
              <option value="">
                {{ text('From item', '起始工作项') }}
              </option>
              <option v-for="item in graph?.work_items ?? []" :key="item.id" :value="item.id">
                {{ item.title }}
              </option>
            </select>
            <select v-model="edgeForm.to_id" class="w-full border rounded-md bg-background px-3 py-2 text-sm">
              <option value="">
                {{ text('To item', '目标工作项') }}
              </option>
              <option v-for="item in graph?.work_items ?? []" :key="item.id" :value="item.id">
                {{ item.title }}
              </option>
            </select>
            <div class="grid gap-2 sm:grid-cols-2">
              <select v-model="edgeForm.type" class="border rounded-md bg-background px-3 py-2 text-sm">
                <option value="blocks">
                  {{ text('Blocks', '阻塞') }}
                </option>
                <option value="enables">
                  {{ text('Enables', '使能') }}
                </option>
                <option value="conflicts_with">
                  {{ text('Conflicts with', '与之冲突') }}
                </option>
                <option value="validates">
                  {{ text('Validates', '验证') }}
                </option>
                <option value="collaborates_on">
                  {{ text('Collaborates on', '协作') }}
                </option>
              </select>
              <FaInput v-model="edgeForm.note" class="w-full min-w-0" :placeholder="text('Note', '备注')" />
            </div>
            <FaButton class="w-full" :loading="edgeLoading" @click="createEdge">
              <FaIcon name="i-lucide:link" class="mr-2" />
              {{ text('Add dependency', '添加依赖') }}
            </FaButton>
          </div>
          <!-- Existing edges for selected item -->
          <div class="mt-3 space-y-2">
            <div v-for="edge in edgesForSelected" :key="edge.id" class="rounded-md border p-3 text-sm">
              <div class="font-medium">
                {{ itemTitle(edge.from_id) }}
                <span class="mx-1 text-muted-foreground">→</span>
                {{ itemTitle(edge.to_id) }}
              </div>
              <div class="mt-1 flex items-center justify-between gap-2 text-xs">
                <span class="rounded bg-muted px-1.5 py-0.5">{{ edgeTypeLabel(edge.type) }}</span>
                <span class="text-muted-foreground">{{ edge.note || '' }}</span>
                <FaButton size="sm" variant="outline" @click="deleteEdge(edge)">
                  {{ text('Remove', '移除') }}
                </FaButton>
              </div>
            </div>
            <div v-if="edgesForSelected.length === 0 && selectedItemId" class="py-3 text-center text-sm text-muted-foreground">
              {{ text('No dependencies for this item.', '此工作项暂无依赖关系。') }}
            </div>
          </div>
        </section>

        <!-- Scope conflicts -->
        <section v-if="(graph?.scope_conflicts.length ?? 0) > 0" class="border rounded-lg border-red-500/30 bg-red-500/5 p-4">
          <h2 class="text-base font-semibold text-red-600">
            {{ text('Scope Conflicts', '范围冲突') }}
          </h2>
          <pre class="mt-3 max-h-48 overflow-auto rounded bg-muted/40 p-3 text-xs">{{ pretty(graph?.scope_conflicts ?? []) }}</pre>
        </section>
      </aside>
    </div>
  </div>
</template>
