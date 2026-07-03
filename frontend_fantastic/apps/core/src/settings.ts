import { setSettings } from '@fantastic-admin/settings'

export default setSettings({
  app: {
    home: {
      title: 'Overview',
      fullPath: '/',
    },
    account: {
      auth: false,
    },
    copyright: {
      company: 'AgentFactor',
      dates: '2026',
    },
  },
  menu: {
    mode: 'single',
  },
  toolbar: {
    breadcrumb: true,
    fullscreen: false,
    menuSearch: {
      enable: false,
    },
  },
  topbar: {
    tabbar: false,
  },
})
