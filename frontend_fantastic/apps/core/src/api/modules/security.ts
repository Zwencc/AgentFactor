import api from '../index'

export interface SecurityOverview {
  endpoints: number
  integrations: number
  degraded_integrations: number
  events: number
  decisions: Record<string, number>
  action_effects: Record<'record' | 'remind' | 'block', number>
  reminders: number
  blocked: number
}

export interface SecurityIntegration {
  id: string
  project_id: string
  workspace_id: string
  name: string
  status: string
  assurance: string
  last_sequence: number
  head_hash?: string | null
  unresolved_gaps: number
  last_hook_at?: string | null
  last_seen_at?: string | null
  proofs: SecurityCapabilityProof[]
  recent_events?: SecurityEvent[]
}

export interface SecurityCapabilityProof {
  capability: string
  status: 'pass' | 'warn' | 'fail' | 'stale'
  result_status: 'pass' | 'warn' | 'fail'
  code: string
  required: boolean
  observed_at?: string | null
  evidence_refs: string[]
  metrics: Record<string, number>
  attestation: string
  checked_at: string
  expires_at: string
}

export interface SecurityEndpoint {
  id: string
  kind: string
  display_name: string
  runtime_version?: string | null
  protocol_version?: string | null
  status: string
  last_error?: string | null
  last_seen_at?: string | null
  integrations: SecurityIntegration[]
}

export interface SecurityEvent {
  id: number
  integration_id: string
  project_id: string
  sequence: number
  event_type: string
  actor_kind: string
  agent_kind?: string | null
  native_session_id?: string | null
  acr_session_id?: string | null
  decision_kind?: string | null
  contract_ids: string[]
  evidence_refs: string[]
  event_hash: string
  occurred_at: string
  recorded_at: string
  correlation_id?: string | null
  causation_event_id?: string | null
  native_turn_id?: string | null
  native_tool_use_id?: string | null
}

export interface SecurityActionDecision {
  id: string
  action_fingerprint: string
  policy_digest: string
  policy_version: string
  effect: 'record' | 'remind' | 'block'
  reason_code: string
  rule_id?: string | null
  principal_id: string
  session_id: string
  surface: string
  operation: string
  resource_digest: string
  params_digest: string
  enforced: boolean
  created_at: string
}

export interface SecurityRiskAlert {
  id: string
  decision_id?: string | null
  integration_id?: string | null
  source_event_id?: string | null
  action_fingerprint: string
  effect: 'remind' | 'block'
  reason_code: string
  created_at: string
}

export interface SecurityMCPTool {
  name: string
  schema_digest: string
  status: string
  first_seen_at: string
  last_seen_at: string
}

export interface SecurityMCPServer {
  id: string
  display_name: string
  transport: string
  config_digest: string
  status: string
  last_error?: string | null
  last_discovered_at?: string | null
  tools: SecurityMCPTool[]
}

export interface SecuritySandboxSession {
  id: string
  terminal_id: string
  provider: string
  backend: string
  profile: string
  workspace_digest: string
  status: 'active' | 'completed' | 'failed'
  capabilities: Record<string, 'SANDBOX_ENFORCED' | 'HOOK_ENFORCED' | 'GATEWAY_ENFORCED' | 'CONFIGURED' | 'OBSERVED' | 'NOT_ENFORCED'>
  last_error?: string | null
  started_at?: string | null
  ended_at?: string | null
  created_at: string
}

export default {
  sync: () => api.post('/security/v1alpha1/sync') as Promise<{
    endpoint_id: string
    workspaces: number
    events_ingested: number
  }>,
  overview: () => api.get('/security/v1alpha1/overview') as Promise<SecurityOverview>,
  endpoints: () => api.get('/security/v1alpha1/endpoints') as Promise<SecurityEndpoint[]>,
  endpoint: (endpointId: string) => api.get(`/security/v1alpha1/endpoints/${encodeURIComponent(endpointId)}`) as Promise<SecurityEndpoint>,
  integration: (integrationId: string) => api.get(`/security/v1alpha1/integrations/${encodeURIComponent(integrationId)}`) as Promise<SecurityIntegration>,
  verify: (integrationId: string) => api.post(`/security/v1alpha1/integrations/${encodeURIComponent(integrationId)}/verify`) as Promise<SecurityIntegration>,
  events: (afterId = 0, limit = 200) => api.get('/security/v1alpha1/events', {
    params: { after_id: afterId, limit },
  }) as Promise<SecurityEvent[]>,
  actionDecisions: (limit = 200) => api.get('/security/v1alpha1/action-decisions', {
    params: { limit },
  }) as Promise<SecurityActionDecision[]>,
  alerts: (limit = 200) => api.get('/security/v1alpha1/alerts', {
    params: { limit },
  }) as Promise<SecurityRiskAlert[]>,
  sandboxes: () => api.get('/security/v1alpha1/sandboxes') as Promise<SecuritySandboxSession[]>,
  sandbox: (sandboxId: string) => api.get(`/security/v1alpha1/sandboxes/${encodeURIComponent(sandboxId)}`) as Promise<SecuritySandboxSession>,
  mcpServers: () => api.get('/security/v1alpha1/mcp/servers') as Promise<SecurityMCPServer[]>,
  discoverMcp: () => api.post('/security/v1alpha1/mcp/discover') as Promise<SecurityMCPServer[]>,
}
