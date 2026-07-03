export const useAppAccountStore = defineStore('appAccount', () => {
  const token = ref('local-dashboard')
  const account = ref('operator@localhost')
  const avatar = ref('')
  const fullName = ref('Local Operator')
  const role = ref('operator')
  const tenantId = ref(1)
  const tenantName = ref('AgentFactor')
  const permissions = ref<string[]>(['dashboard:view', 'session:operate'])
  const isLogin = computed(() => true)

  async function login(_payload?: unknown) {}
  function logout(_redirectPath?: string) {}
  function requestLogout() {}
  async function getPermissions() {}
  async function editPassword(_payload?: unknown) {
    faToast.info('This local dashboard does not use account passwords.')
  }
  function lock() {}
  function unlock() {}

  return {
    token,
    account,
    avatar,
    fullName,
    role,
    tenantId,
    tenantName,
    permissions,
    isLogin,
    login,
    logout,
    requestLogout,
    getPermissions,
    editPassword,
    lock,
    unlock,
  }
})
