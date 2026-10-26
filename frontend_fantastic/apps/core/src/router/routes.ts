import type { RouteRecordMainRaw } from '@fantastic-admin/types'
import type { RouteRecordRaw } from 'vue-router'
import { text } from '@/i18n'

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
        redirect: '/security',
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
      ...[
        'overview',
        'tasks',
        'sessions',
        'prompts',
        'approvals',
        'context',
        'terminal-history',
        'work-graph',
        'topology',
        'providers',
        'settings/ai-review',
        'handbook',
      ].map(path => ({ path, redirect: '/security' })),
      {
        path: 'security/decisions',
        redirect: '/security/audit',
      },
      {
        path: 'security/actions',
        redirect: '/security/audit',
      },
    ],
  },
]

const asyncRoutes: RouteRecordMainRaw[] = [
  {
    meta: {
      title: () => text('AgentFactor Security', 'AgentFactor 安全'),
      icon: 'i-lucide:shield',
    },
    children: [
      {
        path: '/security',
        name: 'securityOverview',
        component: () => import('@/views/security-overview.vue'),
        meta: {
          title: () => text('Security Overview', '安全总览'),
          icon: 'i-lucide:shield-check',
        },
      },
      {
        path: '/security/audit',
        name: 'securityAudit',
        component: () => import('@/views/security-audit.vue'),
        meta: {
          title: () => text('Security Audit', '安全审计'),
          icon: 'i-lucide:scroll-text',
        },
      },
      {
        path: '/security/mcp',
        name: 'securityMcp',
        component: () => import('@/views/security-mcp.vue'),
        meta: {
          title: () => text('MCP Gateway', 'MCP 网关'),
          icon: 'i-lucide:blocks',
        },
      },
      {
        path: '/security/sandboxes',
        name: 'securitySandboxes',
        component: () => import('@/views/security-sandboxes.vue'),
        meta: {
          title: () => text('Agent Sandboxes', 'Agent 沙箱'),
          icon: 'i-lucide:box',
        },
      },
      {
        path: '/security/endpoints/:endpointId',
        name: 'securityEndpoint',
        component: () => import('@/views/security-endpoint.vue'),
        meta: {
          title: () => text('Endpoint Detail', '端点详情'),
          menu: false,
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
