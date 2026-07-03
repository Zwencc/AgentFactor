<script setup lang="ts">
import { useLanguage } from '@/i18n'
import { useRouter } from 'vue-router'

defineOptions({
  name: 'ConductorHandbook',
})

const { text } = useLanguage()
const router = useRouter()

const activeSection = ref('overview')

const sections = computed(() => [
  { id: 'overview',     icon: 'i-lucide:map',              label: text('Overview', '系统概览') },
  { id: 'start',        icon: 'i-lucide:terminal',         label: text('Start System', '启动系统') },
  { id: 'plan',         icon: 'i-lucide:git-branch',       label: text('Plan Work', '规划工作项') },
  { id: 'launch',       icon: 'i-lucide:play-square',      label: text('Launch Agents', '启动 Agent 团队') },
  { id: 'assign',       icon: 'i-lucide:user-check',       label: text('Assign Items', '分配工作项') },
  { id: 'monitor',      icon: 'i-lucide:monitor',          label: text('Monitor', '监控执行') },
  { id: 'approval',     icon: 'i-lucide:shield-check',     label: text('Approvals', '审批工作流') },
  { id: 'context',      icon: 'i-lucide:activity',         label: text('Context Health', '上下文健康') },
  { id: 'complete',     icon: 'i-lucide:check-circle',     label: text('Complete Items', '完成工作项') },
  { id: 'topology',     icon: 'i-lucide:network',          label: text('Topology Failsafe', '拓扑兜底') },
  { id: 'reference',    icon: 'i-lucide:book-open',        label: text('CLI Reference', '命令速查') },
])

function scrollTo(id: string) {
  activeSection.value = id
  const el = document.getElementById(`section-${id}`)
  el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function navigate(path: string) {
  router.push(path)
}

// Update active section on scroll
const contentRef = ref<HTMLElement | null>(null)
function onScroll() {
  if (!contentRef.value) return
  const sectionIds = sections.value.map(s => s.id)
  for (const id of [...sectionIds].reverse()) {
    const el = document.getElementById(`section-${id}`)
    if (el && el.getBoundingClientRect().top <= 120) {
      activeSection.value = id
      break
    }
  }
}
</script>

<template>
  <div class="flex h-[calc(100vh-56px)] overflow-hidden">
    <!-- Sidebar -->
    <aside class="hidden w-56 shrink-0 overflow-y-auto border-r bg-background lg:block">
      <div class="sticky top-0 border-b bg-background px-4 py-3">
        <div class="flex items-center gap-2">
          <FaIcon name="i-lucide:book-open" class="size-4 text-primary" />
          <span class="text-sm font-semibold">{{ text('Handbook', '操作手册') }}</span>
        </div>
      </div>
      <nav class="p-2">
        <button
          v-for="section in sections"
          :key="section.id"
          class="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors"
          :class="activeSection === section.id
            ? 'bg-primary/10 text-primary font-medium'
            : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
          @click="scrollTo(section.id)"
        >
          <FaIcon :name="section.icon" class="size-3.5 shrink-0" />
          {{ section.label }}
        </button>
      </nav>
    </aside>

    <!-- Main content -->
    <div ref="contentRef" class="flex-1 overflow-y-auto" @scroll="onScroll">
      <div class="mx-auto max-w-3xl space-y-16 px-6 py-8">

        <!-- Overview -->
        <section :id="`section-overview`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-primary/10">
              <FaIcon name="i-lucide:map" class="size-5 text-primary" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('System Overview', '系统概览') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('Architecture and core concepts', '架构与核心概念') }}</p>
            </div>
          </div>
          <div class="prose-sm space-y-4 text-sm leading-7 text-foreground/80">
            <p>{{ text('AgentFactor is a multi-agent orchestration platform. It runs AI agents (Claude Code, DeepSeek, Codex) inside tmux sessions, coordinates them through an inbox messaging system, and provides a human-in-the-loop approval layer for sensitive operations.',
              'AgentFactor 是一个多智能体编排平台。它在 tmux 会话内运行 AI 智能体（Claude Code、DeepSeek、Codex 等），通过收件箱消息系统协调各 Agent，并为高危操作提供人工审批层。') }}</p>
            <div class="grid gap-3 rounded-lg border bg-muted/20 p-4 md:grid-cols-3">
              <div class="text-center">
                <div class="text-2xl font-bold text-primary">Session</div>
                <div class="mt-1 text-xs text-muted-foreground">{{ text('tmux session = 1 supervisor + N workers', 'tmux 会话 = 1 个主管 + N 个工人') }}</div>
              </div>
              <div class="text-center">
                <div class="text-2xl font-bold text-primary">Terminal</div>
                <div class="mt-1 text-xs text-muted-foreground">{{ text('A single tmux window running one provider', '运行单个 Provider 的 tmux 窗口') }}</div>
              </div>
              <div class="text-center">
                <div class="text-2xl font-bold text-primary">Work Item</div>
                <div class="mt-1 text-xs text-muted-foreground">{{ text('A task unit with dependencies and proof requirements', '带有依赖关系和完成证明要求的任务单元') }}</div>
              </div>
            </div>
            <div class="rounded-lg border-l-4 border-primary bg-primary/5 p-4">
              <div class="font-medium">{{ text('Core flow', '核心流程') }}</div>
              <div class="mt-2 flex flex-wrap items-center gap-2 text-xs">
                <span class="rounded bg-muted px-2 py-1">{{ text('Plan (Work Graph)', '规划（工作图谱）') }}</span>
                <FaIcon name="i-lucide:arrow-right" class="size-3 text-muted-foreground" />
                <span class="rounded bg-muted px-2 py-1">{{ text('Launch (Task Center)', '启动（任务中心）') }}</span>
                <FaIcon name="i-lucide:arrow-right" class="size-3 text-muted-foreground" />
                <span class="rounded bg-muted px-2 py-1">{{ text('Monitor (Sessions)', '监控（会话）') }}</span>
                <FaIcon name="i-lucide:arrow-right" class="size-3 text-muted-foreground" />
                <span class="rounded bg-muted px-2 py-1">{{ text('Verify (Work Graph)', '验收（工作图谱）') }}</span>
              </div>
            </div>
          </div>
        </section>

        <div class="border-t" />

        <!-- Start System -->
        <section :id="`section-start`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-emerald-500/10">
              <FaIcon name="i-lucide:terminal" class="size-5 text-emerald-600" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('Start System', '启动系统') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('Environment setup and server startup', '环境初始化与服务启动') }}</p>
            </div>
          </div>
          <div class="space-y-4 text-sm">
            <div class="rounded-lg border">
              <div class="flex items-center justify-between border-b bg-muted/30 px-4 py-2">
                <span class="font-mono text-xs text-muted-foreground">WSL / bash</span>
                <span class="rounded bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700">{{ text('First time only', '首次运行') }}</span>
              </div>
              <pre class="overflow-x-auto p-4 text-xs leading-6"><code>cd /path/to/agentfactor
acd init           <span class="text-muted-foreground"># 创建 ~/.conductor/ 目录和 SQLite 数据库</span></code></pre>
            </div>
            <div class="rounded-lg border">
              <div class="border-b bg-muted/30 px-4 py-2 font-mono text-xs text-muted-foreground">WSL / bash — {{ text('every session', '每次启动') }}</div>
              <pre class="overflow-x-auto p-4 text-xs leading-6"><code>cd /path/to/agentfactor
uv run uvicorn agentfactor.api.main:app --reload --port 9889
<span class="text-muted-foreground"># 保持此终端运行，打开浏览器访问 http://127.0.0.1:9889</span></code></pre>
            </div>
            <div class="rounded-lg border-l-4 border-amber-400 bg-amber-50/50 p-3 text-xs dark:bg-amber-950/20">
              <span class="font-medium text-amber-700">{{ text('Note', '注意') }}：</span>
              <span class="text-foreground/70">{{ text('All CLI commands (acd) also require the server to be running. Run acd health to verify.', 'CLI 命令（acd）同样依赖服务器运行。可通过 acd health 验证连接状态。') }}</span>
            </div>
          </div>
        </section>

        <div class="border-t" />

        <!-- Plan Work -->
        <section :id="`section-plan`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-blue-500/10">
              <FaIcon name="i-lucide:git-branch" class="size-5 text-blue-600" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('Plan Work Items', '规划工作项') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('Create a project and define the causal dependency graph', '创建项目，定义因果依赖图') }}</p>
            </div>
          </div>
          <div class="space-y-4 text-sm leading-7 text-foreground/80">
            <ol class="space-y-3">
              <li class="flex gap-3">
                <span class="flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-500/10 text-xs font-bold text-blue-600">1</span>
                <span>{{ text('Open Work Graph in the sidebar. Type a new Project ID in the topbar selector and press Enter — this creates an implicit project namespace.', '打开侧边栏中的「工作图谱」。在顶栏选择器输入新的 Project ID 后按回车，这将创建一个隐式项目命名空间。') }}</span>
              </li>
              <li class="flex gap-3">
                <span class="flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-500/10 text-xs font-bold text-blue-600">2</span>
                <span>{{ text('Click "Create work item". Fill in title, type, priority (1-5), complexity (1-5). Add acceptance criteria line by line.', '点击「创建工作项」。填写标题、类型、优先级（1-5）、复杂度（1-5），逐行填写验收标准。') }}</span>
              </li>
              <li class="flex gap-3">
                <span class="flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-500/10 text-xs font-bold text-blue-600">3</span>
                <span>{{ text('After creating all items, use "Add dependency edge" to define BLOCKS relationships between items. The system automatically computes the critical path.', '创建完所有工作项后，使用「添加依赖边」定义 BLOCKS 关系。系统会自动计算关键路径。') }}</span>
              </li>
            </ol>
            <div class="rounded-lg border bg-muted/10 p-4">
              <div class="mb-2 text-xs font-semibold text-muted-foreground">{{ text('Work item types and their proof requirements', '工作项类型与对应的完成证明要求') }}</div>
              <div class="grid gap-2 text-xs md:grid-cols-2">
                <div v-for="row in [
                  { type: 'feature', zh: '新功能', proof: 'git_commit + test_pass' },
                  { type: 'bugfix', zh: '缺陷修复', proof: 'git_commit + bug_not_reproduced' },
                  { type: 'refactor', zh: '代码重构', proof: 'git_commit + test_pass' },
                  { type: 'test', zh: '测试验证', proof: 'git_commit' },
                  { type: 'review', zh: '代码审查', proof: 'reviewer_signoff' },
                  { type: 'investigation', zh: '调研分析', proof: 'completion_signal' },
                ]" :key="row.type" class="flex items-center justify-between rounded border bg-background px-2 py-1">
                  <span class="font-medium">{{ row.type }} <span class="font-normal text-muted-foreground">/ {{ row.zh }}</span></span>
                  <span class="text-muted-foreground">{{ row.proof }}</span>
                </div>
              </div>
            </div>
            <button class="flex items-center gap-1.5 text-xs text-primary hover:underline" @click="navigate('/work-graph')">
              <FaIcon name="i-lucide:arrow-right" class="size-3" />
              {{ text('Go to Work Graph →', '前往工作图谱 →') }}
            </button>
          </div>
        </section>

        <div class="border-t" />

        <!-- Launch Agents -->
        <section :id="`section-launch`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-violet-500/10">
              <FaIcon name="i-lucide:play-square" class="size-5 text-violet-600" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('Launch Agent Team', '启动 Agent 团队') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('Create a session with supervisor + workers via Task Center or CLI', '通过任务中心或 CLI 创建含主管与工人的会话') }}</p>
            </div>
          </div>
          <div class="space-y-4 text-sm leading-7 text-foreground/80">
            <p>{{ text('Task Center provides a UI form. The CLI is equivalent and useful for scripts.', '任务中心提供图形表单，CLI 等价且适合脚本调用。') }}</p>
            <div class="rounded-lg border">
              <div class="border-b bg-muted/30 px-4 py-2 font-mono text-xs text-muted-foreground">CLI</div>
              <pre class="overflow-x-auto p-4 text-xs leading-6"><code><span class="text-muted-foreground"># 启动一个 supervisor + 自动工人</span>
acd launch --agent-profile conductor \
           --working-dir /path/to/project

<span class="text-muted-foreground"># 查看启动的会话</span>
acd sessions

<span class="text-muted-foreground"># 手动追加工人</span>
acd worker &lt;session-name&gt; --agent-profile developer</code></pre>
            </div>
            <div class="grid gap-3 text-xs md:grid-cols-3">
              <div v-for="preset in [
                { name: 'engineering', zh: '工程预设', workers: 'developer + reviewer + tester' },
                { name: 'qa', zh: 'QA 预设', workers: 'deepseek_tester' },
                { name: 'solo', zh: '单人预设', workers: text('supervisor only', '仅主管') },
              ]" :key="preset.name" class="rounded-lg border p-3">
                <div class="font-semibold">{{ preset.name }} <span class="font-normal text-muted-foreground">/ {{ preset.zh }}</span></div>
                <div class="mt-1 text-muted-foreground">{{ preset.workers }}</div>
              </div>
            </div>
            <button class="flex items-center gap-1.5 text-xs text-primary hover:underline" @click="navigate('/tasks')">
              <FaIcon name="i-lucide:arrow-right" class="size-3" />
              {{ text('Go to Task Center →', '前往任务中心 →') }}
            </button>
          </div>
        </section>

        <div class="border-t" />

        <!-- Assign Items -->
        <section :id="`section-assign`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-orange-500/10">
              <FaIcon name="i-lucide:user-check" class="size-5 text-orange-600" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('Assign Work Items to Terminals', '分配工作项到终端') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('Link each work item to the terminal responsible for it', '把每个工作项与负责执行的终端关联') }}</p>
            </div>
          </div>
          <div class="space-y-4 text-sm leading-7 text-foreground/80">
            <ol class="space-y-3">
              <li class="flex gap-3">
                <span class="flex size-6 shrink-0 items-center justify-center rounded-full bg-orange-500/10 text-xs font-bold text-orange-600">1</span>
                <span>{{ text('In Work Graph, click a work item to open its detail panel on the right.', '在工作图谱中点击工作项，右侧面板会展开详情。') }}</span>
              </li>
              <li class="flex gap-3">
                <span class="flex size-6 shrink-0 items-center justify-center rounded-full bg-orange-500/10 text-xs font-bold text-orange-600">2</span>
                <span>{{ text('In the "Owner Terminal" dropdown, select the terminal you want to assign. The list shows all active terminals from the current sessions.', '在「负责终端」下拉框里选择要分配的终端，列表来自当前活跃会话。') }}</span>
              </li>
              <li class="flex gap-3">
                <span class="flex size-6 shrink-0 items-center justify-center rounded-full bg-orange-500/10 text-xs font-bold text-orange-600">3</span>
                <span>{{ text('After assignment, the Sessions page will show work item badges on each terminal card — immediately visible which terminal is doing what.', '分配后，Sessions 页面的每张终端卡片上会显示工作项标签，一目了然哪个终端在做什么任务。') }}</span>
              </li>
            </ol>
            <div class="rounded-lg border-l-4 border-blue-400 bg-blue-50/50 p-3 text-xs dark:bg-blue-950/20">
              <span class="font-medium text-blue-700">{{ text('Tip', '提示') }}：</span>
              <span class="text-foreground/70">{{ text('A terminal can own multiple work items. The supervisor typically manages no items directly — it delegates to workers.', '一个终端可以负责多个工作项。主管通常不直接拥有工作项，而是把任务分发给工人。') }}</span>
            </div>
          </div>
        </section>

        <div class="border-t" />

        <!-- Monitor -->
        <section :id="`section-monitor`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-sky-500/10">
              <FaIcon name="i-lucide:monitor" class="size-5 text-sky-600" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('Monitor Execution', '监控执行') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('Watch terminal output, send messages, and manage the session lifecycle', '查看终端输出、发送消息、管理会话生命周期') }}</p>
            </div>
          </div>
          <div class="space-y-4 text-sm leading-7 text-foreground/80">
            <div class="grid gap-3 md:grid-cols-2">
              <div v-for="item in [
                { icon: 'i-lucide:eye', title: text('Terminal output', '终端输出'), desc: text('Click any terminal card to view live output in the right panel. Enable Auto-refresh to keep it current.', '点击终端卡片在右侧面板查看实时输出，勾选「自动刷新」保持最新。') },
                { icon: 'i-lucide:send', title: text('Send message', '发送消息'), desc: text('Use the message input to inject instructions directly into a terminal\'s stdin. Works like typing in tmux.', '通过消息输入框直接向终端 stdin 注入指令，效果等同于在 tmux 中直接输入。') },
                { icon: 'i-lucide:external-link', title: text('Attach to tmux', '接管 tmux'), desc: text('Run acd attach <session> in a local terminal to take full interactive control of the session.', '在本地终端运行 acd attach <session> 获取完整的交互式控制权。') },
                { icon: 'i-lucide:x-circle', title: text('Close terminal', '关闭终端'), desc: text('The × button closes a single terminal window. Close session removes the entire tmux session.', '× 按钮关闭单个终端窗口，「关闭会话」移除整个 tmux 会话。') },
              ]" :key="item.title" class="rounded-lg border p-3">
                <div class="flex items-center gap-2">
                  <FaIcon :name="item.icon" class="size-3.5 text-muted-foreground" />
                  <span class="font-medium">{{ item.title }}</span>
                </div>
                <p class="mt-1.5 text-xs text-muted-foreground">{{ item.desc }}</p>
              </div>
            </div>
            <div class="rounded-lg border">
              <div class="border-b bg-muted/30 px-4 py-2 font-mono text-xs text-muted-foreground">CLI {{ text('shortcuts', '快捷命令') }}</div>
              <pre class="overflow-x-auto p-4 text-xs leading-6"><code>acd ls                         <span class="text-muted-foreground"># 列出所有会话</span>
acd out &lt;terminal-id&gt;          <span class="text-muted-foreground"># 获取最后输出</span>
acd s &lt;terminal-id&gt; -m "msg"   <span class="text-muted-foreground"># 发送消息</span>
acd logs &lt;terminal-id&gt; -f      <span class="text-muted-foreground"># 跟踪日志</span>
acd a &lt;session-name&gt;           <span class="text-muted-foreground"># 接管 tmux 会话</span></code></pre>
            </div>
            <button class="flex items-center gap-1.5 text-xs text-primary hover:underline" @click="navigate('/sessions')">
              <FaIcon name="i-lucide:arrow-right" class="size-3" />
              {{ text('Go to Sessions →', '前往终端会话 →') }}
            </button>
          </div>
        </section>

        <div class="border-t" />

        <!-- Approval -->
        <section :id="`section-approval`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-amber-500/10">
              <FaIcon name="i-lucide:shield-check" class="size-5 text-amber-600" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('Approval Workflow', '审批工作流') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('Human-in-the-loop gate for sensitive operations', '针对高危操作的人工审核关卡') }}</p>
            </div>
          </div>
          <div class="space-y-4 text-sm leading-7 text-foreground/80">
            <p>{{ text('When an agent needs to perform a potentially destructive action, it can flag the command for supervisor review before execution.', '当 Agent 需要执行潜在高危操作时，可将该命令标记为需要主管审核，审核通过后才会执行。') }}</p>
            <div class="flex flex-col gap-2">
              <div v-for="(step, i) in [
                text('Agent sends command with --require-approval flag (or via MCP request_approval tool)', 'Agent 使用 --require-approval 标志发送命令（或通过 MCP request_approval 工具）'),
                text('Supervisor receives inbox notification about the pending request', '主管收到收件箱通知，提示有待审批请求'),
                text('Human reviews in the Approvals page: Accept or Reject with optional reason', '人工在「审批」页面处理：接受或拒绝（可附原因）'),
                text('Accepted: command is executed in the target terminal. Rejected: agent receives the rejection reason.', '接受：命令在目标终端执行。拒绝：Agent 收到拒绝原因。'),
                text('All decisions are written to ~/.conductor/approvals/audit.log', '所有决策写入 ~/.conductor/approvals/audit.log 审计日志'),
              ]" :key="i" class="flex items-start gap-3 rounded-lg border bg-background px-3 py-2.5">
                <span class="flex size-5 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-xs font-bold text-amber-600">{{ i + 1 }}</span>
                <span class="text-xs">{{ step }}</span>
              </div>
            </div>
            <div class="rounded-lg border">
              <div class="border-b bg-muted/30 px-4 py-2 font-mono text-xs text-muted-foreground">CLI</div>
              <pre class="overflow-x-auto p-4 text-xs leading-6"><code>acd approvals --status PENDING       <span class="text-muted-foreground"># 查看待审批</span>
acd approve &lt;request-id&gt;            <span class="text-muted-foreground"># 批准</span>
acd deny &lt;request-id&gt; -r "reason"   <span class="text-muted-foreground"># 拒绝</span></code></pre>
            </div>
            <button class="flex items-center gap-1.5 text-xs text-primary hover:underline" @click="navigate('/approvals')">
              <FaIcon name="i-lucide:arrow-right" class="size-3" />
              {{ text('Go to Approvals →', '前往审批页面 →') }}
            </button>
          </div>
        </section>

        <div class="border-t" />

        <!-- Context Health -->
        <section :id="`section-context`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-teal-500/10">
              <FaIcon name="i-lucide:activity" class="size-5 text-teal-600" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('Context Health', '上下文健康') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('Monitor context packs and compaction snapshots', '监控上下文包与压缩快照状态') }}</p>
            </div>
          </div>
          <div class="space-y-4 text-sm leading-7 text-foreground/80">
            <div class="grid gap-3 md:grid-cols-2">
              <div class="rounded-lg border p-3">
                <div class="font-semibold">{{ text('Context Pack', '上下文包') }}</div>
                <p class="mt-1 text-xs text-muted-foreground">{{ text('A semantic snapshot of what a terminal is working on. Built on demand or triggered automatically when context loss is detected (≥3 signals in 5 min).', '终端当前工作内容的语义快照。按需生成，或在检测到上下文丢失时自动触发（5 分钟内 ≥3 个信号）。') }}</p>
              </div>
              <div class="rounded-lg border p-3">
                <div class="font-semibold">{{ text('Compaction Snapshot', '压缩快照') }}</div>
                <p class="mt-1 text-xs text-muted-foreground">{{ text('Incremental summary of event history. Runs every 5 min in background. Auto-triggers when event delta exceeds threshold or pack is >24h stale.', '事件历史的增量摘要。后台每 5 分钟运行一次，在事件 delta 超过阈值或上下文包超 24 小时未更新时自动触发。') }}</p>
              </div>
            </div>
            <div class="rounded-lg border bg-muted/10 p-4">
              <div class="mb-3 text-xs font-semibold">{{ text('Key metrics to watch', '需要关注的关键指标') }}</div>
              <div class="space-y-2 text-xs">
                <div class="flex items-center justify-between gap-3">
                  <span class="font-medium">{{ text('Velocity (tpm)', '输出速度 (tpm)') }}</span>
                  <div class="flex gap-2">
                    <span class="rounded bg-red-100 px-1.5 py-0.5 text-red-700 dark:bg-red-950 dark:text-red-400">&lt;10 {{ text('stalled', '卡住') }}</span>
                    <span class="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700 dark:bg-amber-950 dark:text-amber-400">10-30 {{ text('slow', '偏慢') }}</span>
                    <span class="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400">&gt;30 {{ text('healthy', '正常') }}</span>
                  </div>
                </div>
                <div class="flex items-center justify-between gap-3">
                  <span class="font-medium">{{ text('Error density', '错误密度') }}</span>
                  <div class="flex gap-2">
                    <span class="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400">&lt;10% {{ text('healthy', '正常') }}</span>
                    <span class="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700 dark:bg-amber-950 dark:text-amber-400">10-30% {{ text('watch', '关注') }}</span>
                    <span class="rounded bg-red-100 px-1.5 py-0.5 text-red-700 dark:bg-red-950 dark:text-red-400">&gt;30% {{ text('alert', '告警') }}</span>
                  </div>
                </div>
                <div class="flex items-center justify-between gap-3">
                  <span class="font-medium">{{ text('Pack age', '上下文包时效') }}</span>
                  <span class="text-muted-foreground">{{ text('Generate manually if >1h and agent seems confused', '若超 1h 且 Agent 看起来迷失方向，手动生成一次') }}</span>
                </div>
              </div>
            </div>
            <button class="flex items-center gap-1.5 text-xs text-primary hover:underline" @click="navigate('/context')">
              <FaIcon name="i-lucide:arrow-right" class="size-3" />
              {{ text('Go to Overseer Console →', '前往运行监控台 →') }}
            </button>
          </div>
        </section>

        <div class="border-t" />

        <!-- Complete Items -->
        <section :id="`section-complete`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-emerald-500/10">
              <FaIcon name="i-lucide:check-circle" class="size-5 text-emerald-600" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('Complete Work Items', '完成工作项') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('"Done without proof does not exist"', '"没有证明的完成不存在"') }}</p>
            </div>
          </div>
          <div class="space-y-4 text-sm leading-7 text-foreground/80">
            <div class="flex flex-col gap-2">
              <div v-for="(step, i) in [
                { label: text('IN PROGRESS', '进行中'), color: 'bg-blue-500/10 text-blue-700', desc: text('Agent is executing the task. Status set automatically or manually.', 'Agent 正在执行任务，状态由 Agent 自动更新或手动设置。') },
                { label: text('NEEDS VERIFICATION', '待验证'), color: 'bg-amber-500/10 text-amber-700', desc: text('Transitioning here opens a Proof Window. The system now expects evidence to be collected.', '转为此状态时会自动开启一个「证明窗口」，系统开始等待收集证明材料。') },
                { label: text('PROOF COLLECTED', '证明收集'), color: 'bg-violet-500/10 text-violet-700', desc: text('The proof_collector service records: git commits, test results, reviewer sign-offs, etc.', 'proof_collector 服务自动记录：git commit、测试结果、审查人签字等。') },
                { label: text('DONE', '已完成'), color: 'bg-emerald-500/10 text-emerald-700', desc: text('Can ONLY be set after a closed proof window with collected evidence. Backend enforces this — HTTP 422 if bypassed.', '只有在有证明的已关闭证明窗口后才能设置。后端强制执行此规则——跳过会收到 HTTP 422 错误。') },
              ]" :key="i" class="flex items-start gap-3 rounded-lg border px-3 py-2.5">
                <span class="mt-0.5 rounded px-1.5 py-0.5 text-xs font-bold" :class="step.color">{{ step.label }}</span>
                <span class="text-xs">{{ step.desc }}</span>
              </div>
            </div>
          </div>
        </section>

        <div class="border-t" />

        <!-- Topology Failsafe -->
        <section :id="`section-topology`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-red-500/10">
              <FaIcon name="i-lucide:network" class="size-5 text-red-600" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('Topology Failsafe', '拓扑兜底') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('Automatic team restructuring when performance degrades', '性能下降时的自动团队调整建议') }}</p>
            </div>
          </div>
          <div class="space-y-4 text-sm leading-7 text-foreground/80">
            <p>{{ text('The Topology Engine runs in the background every 10 seconds, analyzing terminal metrics. When thresholds are breached it generates a Proposal — which you review and accept or reject.', '拓扑引擎每 10 秒在后台运行一次，分析终端指标。当超出阈值时生成「调整建议」，由你来审核接受或拒绝。') }}</p>
            <div class="grid gap-3 text-xs md:grid-cols-3">
              <div v-for="p in [
                { type: 'investigate', icon: 'i-lucide:search', color: 'text-amber-600 bg-amber-500/10', title: text('Investigate', '调查排查'), desc: text('Low velocity or high idle — agent may be stuck', '速度低或长时间空闲，Agent 可能卡住') },
                { type: 'replace_provider', icon: 'i-lucide:refresh-cw', color: 'text-red-600 bg-red-500/10', title: text('Replace Provider', '更换执行方'), desc: text('High error density — switch to a different model', '错误率高，建议切换到不同模型') },
                { type: 'add_worker', icon: 'i-lucide:user-plus', color: 'text-blue-600 bg-blue-500/10', title: text('Add Worker', '增派智能体'), desc: text('Workload too high for current team size', '当前团队规模难以承载工作量') },
              ]" :key="p.type" class="rounded-lg border p-3">
                <div class="mb-1.5 flex items-center gap-2">
                  <div class="flex size-6 items-center justify-center rounded" :class="p.color">
                    <FaIcon :name="p.icon" class="size-3.5" />
                  </div>
                  <span class="font-semibold">{{ p.title }}</span>
                </div>
                <p class="text-muted-foreground">{{ p.desc }}</p>
              </div>
            </div>
            <div class="rounded-lg border-l-4 border-red-400 bg-red-50/50 p-3 text-xs dark:bg-red-950/20">
              <span class="font-medium text-red-700">{{ text('Note', '注意') }}：</span>
              <span class="text-foreground/70">{{ text('Accepting a replace_provider proposal does NOT automatically restart the terminal. You still need to close the old terminal and launch a new one with the suggested provider.', '接受 replace_provider 建议不会自动重启终端。你仍需手动关闭旧终端，并用建议的 Provider 启动新终端。') }}</span>
            </div>
            <button class="flex items-center gap-1.5 text-xs text-primary hover:underline" @click="navigate('/topology')">
              <FaIcon name="i-lucide:arrow-right" class="size-3" />
              {{ text('Go to Topology →', '前往团队拓扑 →') }}
            </button>
          </div>
        </section>

        <div class="border-t" />

        <!-- CLI Reference -->
        <section :id="`section-reference`">
          <div class="mb-6 flex items-center gap-3">
            <div class="flex size-10 items-center justify-center rounded-lg bg-slate-500/10">
              <FaIcon name="i-lucide:book-open" class="size-5 text-slate-600" />
            </div>
            <div>
              <h2 class="text-xl font-bold">{{ text('CLI Quick Reference', 'CLI 命令速查') }}</h2>
              <p class="text-sm text-muted-foreground">{{ text('All acd / agentfactor commands', '所有 acd / agentfactor 命令') }}</p>
            </div>
          </div>
          <div class="space-y-4 text-sm">
            <div v-for="group in [
              {
                title: text('Session management', '会话管理'),
                rows: [
                  { cmd: 'acd launch --agent-profile <profile>', desc: text('Start a new supervisor session', '启动新主管会话') },
                  { cmd: 'acd worker <session> --agent-profile <p>', desc: text('Spawn a worker in a session', '在会话中添加工人') },
                  { cmd: 'acd sessions / acd ls', desc: text('List all sessions', '列出所有会话') },
                  { cmd: 'acd session <name>', desc: text('Detailed session view', '查看单个会话详情') },
                  { cmd: 'acd kill <session> -f', desc: text('Kill entire session', '强制终止整个会话') },
                  { cmd: 'acd close <id> / acd rm <id>', desc: text('Close a single terminal', '关闭单个终端') },
                  { cmd: 'acd attach <session> / acd a', desc: text('Attach to tmux session', '接管 tmux 会话') },
                ],
              },
              {
                title: text('I/O and messaging', '输入输出与消息'),
                rows: [
                  { cmd: 'acd send <id> -m <msg> / acd s', desc: text('Send message to terminal', '向终端发送消息') },
                  { cmd: 'acd output <id> / acd out', desc: text('Get terminal output', '获取终端输出') },
                  { cmd: 'acd logs <id> [-f] [-n N]', desc: text('View terminal logs', '查看终端日志') },
                  { cmd: 'acd status <id>', desc: text('Quick terminal status', '快速查看终端状态') },
                ],
              },
              {
                title: text('Approvals', '审批'),
                rows: [
                  { cmd: 'acd approvals --status PENDING', desc: text('List pending approvals', '列出待审批请求') },
                  { cmd: 'acd approve <request-id>', desc: text('Approve a request', '批准请求') },
                  { cmd: 'acd deny <request-id> -r <reason>', desc: text('Deny a request', '拒绝请求') },
                ],
              },
              {
                title: text('Persona management', '角色管理'),
                rows: [
                  { cmd: 'acd persona list', desc: text('List all personas', '列出所有 Persona') },
                  { cmd: 'acd persona show <name>', desc: text('Show persona detail', '查看 Persona 详情') },
                  { cmd: 'acd persona edit <name>', desc: text('Edit persona file', '编辑 Persona 文件') },
                  { cmd: 'acd persona create <name>', desc: text('Create new persona', '创建新 Persona') },
                ],
              },
            ]" :key="group.title" class="rounded-lg border">
              <div class="border-b bg-muted/30 px-4 py-2 text-xs font-semibold text-muted-foreground">{{ group.title }}</div>
              <div class="divide-y">
                <div v-for="row in group.rows" :key="row.cmd" class="flex items-baseline gap-4 px-4 py-2 text-xs">
                  <code class="w-72 shrink-0 font-mono text-primary">{{ row.cmd }}</code>
                  <span class="text-muted-foreground">{{ row.desc }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <div class="h-16" />
      </div>
    </div>
  </div>
</template>
