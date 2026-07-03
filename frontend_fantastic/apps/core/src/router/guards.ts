import type { Router } from 'vue-router'
import { useNProgress } from '@vueuse/integrations/useNProgress'
import { warnKeepAliveComponentNameMissing } from 'virtual:fantastic-admin/turbo-console'
import { asyncRoutes } from './routes'
import '@/assets/styles/nprogress.css'

function setupRoutes(router: Router) {
  router.beforeEach(async (to) => {
    const appSettingsStore = useAppSettingsStore()
    const appRouteStore = useAppRouteStore()
    const appMenuStore = useAppMenuStore()

    if (!appRouteStore.isGenerate) {
      appRouteStore.generateRoutesAtFront(asyncRoutes)
      const removeRoutes: (() => void)[] = []
      // System routes (including the 'root' layout) must be registered first so
      // that addRoute('root', child) finds the parent and nests correctly.
      appRouteStore.systemRoutes.forEach((route) => {
        removeRoutes.push(router.addRoute(route))
      })
      appRouteStore.routes.forEach((route) => {
        if (!/^(?:https?:|mailto:|tel:)/.test(route.path)) {
          removeRoutes.push(router.addRoute('root', route))
        }
      })
      appRouteStore.setCurrentRemoveRoutes(removeRoutes)
      return {
        path: to.path,
        query: to.query,
        replace: true,
      }
    }

    appSettingsStore.settings.menu.mode !== 'single' && appMenuStore.setActived(to.path)
  })
}

function setupRedirectAuthChildrenRoute(router: Router) {
  router.beforeEach((to) => {
    const currentRoute = router.getRoutes().find(route => route.path === (to.matched.at(-1)?.path ?? ''))
    if (!currentRoute?.redirect) {
      const findRoute = currentRoute?.children?.find(route => route.meta?.menu !== false)
      if (findRoute) {
        return findRoute
      }
    }
  })
}

function setupProgress(router: Router) {
  const { isLoading } = useNProgress()
  router.beforeEach(() => {
    const appSettingsStore = useAppSettingsStore()
    if (appSettingsStore.settings.page.progress) {
      isLoading.value = true
    }
  })
  router.afterEach(() => {
    const appSettingsStore = useAppSettingsStore()
    if (appSettingsStore.settings.page.progress) {
      isLoading.value = false
    }
  })
}

function setupTitle(router: Router) {
  router.afterEach((to) => {
    const appSettingsStore = useAppSettingsStore()
    appSettingsStore.setTitle(to.matched?.at(-1)?.meta?.title ?? to.meta.title)
  })
}

function setupKeepAlive(router: Router) {
  router.afterEach(async (to, from) => {
    const appKeepAliveStore = useAppKeepAliveStore()
    if (to.meta.keepAlive) {
      const componentName = to.matched.at(-1)?.components?.default.name
      if (componentName) {
        let shouldClear = false
        if (typeof to.meta.keepAlive === 'boolean') {
          shouldClear = !to.meta.keepAlive
        }
        else if (typeof to.meta.keepAlive === 'string') {
          shouldClear = to.meta.keepAlive !== from.name
        }
        else if (Array.isArray(to.meta.keepAlive)) {
          shouldClear = !to.meta.keepAlive.includes(from.name as string)
        }
        if (from.name === 'reload') {
          shouldClear = true
        }
        if (shouldClear) {
          appKeepAliveStore.remove(componentName)
          await nextTick()
        }
        appKeepAliveStore.add(componentName)
      }
      else if (import.meta.env.DEV) {
        warnKeepAliveComponentNameMissing((to.matched.at(-1)?.components?.default as any).__file)
      }
    }
  })
}

function setupOther(router: Router) {
  router.afterEach(() => {
    document.documentElement.scrollTop = 0
  })
}

export default function setupGuards(router: Router) {
  setupRoutes(router)
  setupRedirectAuthChildrenRoute(router)
  setupProgress(router)
  setupTitle(router)
  setupKeepAlive(router)
  setupOther(router)
}
