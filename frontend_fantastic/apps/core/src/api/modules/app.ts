export interface AuthResponse {
  ok: boolean
  access_token: string
  user: {
    id: number
    email: string
    role: string
    full_name: string
  }
  tenant: {
    id: number
    name: string
  }
}

export default {
  routeList: () => Promise.resolve({ data: [] }),
  login: async (_data?: unknown) => ({
    ok: true,
    access_token: 'local-dashboard',
    user: {
      id: 1,
      email: 'operator@localhost',
      role: 'operator',
      full_name: 'Local Operator',
    },
    tenant: {
      id: 1,
      name: 'AgentFactor',
    },
  }),
  me: async () => ({
    ok: true,
    access_token: 'local-dashboard',
    user: {
      id: 1,
      email: 'operator@localhost',
      role: 'operator',
      full_name: 'Local Operator',
    },
    tenant: {
      id: 1,
      name: 'AgentFactor',
    },
  }),
  logout: async () => ({}),
}
