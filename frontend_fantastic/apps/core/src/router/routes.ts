import type { RouteRecordMainRaw } from '@fantastic-admin/types'
import type { RouteRecordRaw } from 'vue-router'
import { text } from '@/i18n'
import pinia from '@/store'

function Layout() {
  return import('@/layouts/index.vue')
}

const constantRoutes: RouteRecordRaw[] = [
  {
    path: '/:all(.*)*',
    name: 'notFound',
    component: () => import('@/views/[...all].vue'),
    meta: {
      title: 'Not Found',
    },
  },
]

const systemRoutes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'root',
    component: Layout,
    meta: {
      breadcrumb: false,
    },
    children: [
      {
        path: '',
        redirect: '/overview',
      },
      {
        path: 'reload',
        name: 'reload',
        component: () => import('@/views/reload.vue'),
        meta: {
          title: 'Reloading...',
          breadcrumb: false,
        },
      },
    ],
  },
]

const asyncRoutes: RouteRecordMainRaw[] = [
  {
    meta: {
      title: () => text(useAppSettingsStore(pinia).settings.app.home.title, '总览'),
      icon: 'i-lucide:layout-dashboard',
    },
    children: [
      {
        path: '/overview',
        name: 'overview',
        component: () => import('@/views/index.vue'),
        meta: {
          title: () => text(useAppSettingsStore(pinia).settings.app.home.title, '总览'),
          icon: 'i-lucide:layout-dashboard',
        },
      },
    ],
  },
  {
    meta: {
      title: () => text('Task Center', '任务中心'),
      icon: 'i-lucide:play-square',
    },
    children: [
      {
        path: '/tasks',
        name: 'tasks',
        component: () => import('@/views/tasks.vue'),
        meta: {
          title: () => text('Task Center', '任务中心'),
          icon: 'i-lucide:play-square',
        },
      },
    ],
  },
  {
    meta: {
      title: () => text('AgentFactor', '智能体控制台'),
      icon: 'i-lucide:bot',
    },
    children: [
      {
        path: '/sessions',
        name: 'sessions',
        component: () => import('@/views/sessions.vue'),
        meta: {
          title: () => text('Sessions', '终端会话'),
          icon: 'i-lucide:terminal-square',
        },
      },
      {
        path: '/prompts',
        name: 'prompts',
        component: () => import('@/views/prompts.vue'),
        meta: {
          title: () => text('Prompts', '协作提示'),
          icon: 'i-lucide:message-square-reply',
        },
      },
      {
        path: '/approvals',
        name: 'approvals',
        component: () => import('@/views/approvals.vue'),
        meta: {
          title: () => text('Approvals', '审批'),
          icon: 'i-lucide:shield-check',
        },
      },
      {
        path: '/context',
        name: 'context',
        component: () => import('@/views/context.vue'),
        meta: {
          title: () => text('Overseer Console', '运行监控台'),
          icon: 'i-lucide:activity',
        },
      },
      {
        path: '/terminal-history',
        name: 'terminalHistory',
        component: () => import('@/views/terminal-history.vue'),
        meta: {
          title: () => text('Terminal History', '终端历史分析'),
          icon: 'i-lucide:history',
        },
      },
      {
        path: '/work-graph',
        name: 'workGraph',
        component: () => import('@/views/work-graph.vue'),
        meta: {
          title: () => text('Work Graph', '工作图谱'),
          icon: 'i-lucide:git-branch',
        },
      },
      {
        path: '/topology',
        name: 'topology',
        component: () => import('@/views/topology.vue'),
        meta: {
          title: () => text('Topology', '团队拓扑'),
          icon: 'i-lucide:network',
        },
      },
      {
        path: '/providers',
        name: 'providers',
        component: () => import('@/views/providers.vue'),
        meta: {
          title: () => text('Providers', '运行环境'),
          icon: 'i-lucide:plug-zap',
        },
      },
      {
        path: '/settings/ai-review',
        name: 'aiReviewSettings',
        component: () => import('@/views/ai-review.vue'),
        meta: {
          title: () => text('AI Review', 'AI 审查'),
          icon: 'i-lucide:badge-check',
        },
      },
      {
        path: '/handbook',
        name: 'handbook',
        component: () => import('@/views/handbook.vue'),
        meta: {
          title: () => text('Handbook', '操作手册'),
          icon: 'i-lucide:book-open',
        },
      },
    ],
  },
]

export {
  asyncRoutes,
  constantRoutes,
  systemRoutes,
}
