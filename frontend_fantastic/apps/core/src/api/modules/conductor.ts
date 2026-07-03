import api from '../index'

export interface Terminal {
  id: string
  session_name: string
  window_name: string
  provider: string
  agent_profile?: string | null
  status: 'READY' | 'RUNNING' | 'COMPLETED' | 'ERROR'
  created_at?: string | null
}

export interface Session {
  name: string
  terminals: Terminal[]
}

export interface Approval {
  id: number
  terminal_id: string
  supervisor_id: string
  command_text: string
  metadata_payload?: string | null
  status: 'PENDING' | 'APPROVED' | 'DENIED'
  created_at?: string | null
  decided_at?: string | null
  terminal?: TerminalReference | null
  supervisor?: TerminalReference | null
  risk_hints?: string[]
}

export interface TerminalReference {
  id: string
  label: string
  session_name: string
}

export interface PromptItem {
  id: number
  supervisor_id: string
  target_id?: string | null
  target?: TerminalReference | null
  title: string
  body: string
  created_at?: string | null
}

export interface ProviderHealth {
  key: string
  label: string
  binary: string
  available: boolean
  reason: string
  mcp_capable: boolean
}

export interface BlueprintItem {
  id: string
  title: string
  description: string
  category: string
  priority: number
  effort_hours: number
  depends_on: string[]
  proof_requirements: string[]
  acceptance_criteria: string[]
  files_of_interest: string[]
}

export interface BlueprintResult {
  terminal_id: string
  project_id: string
  status: string
  created_at: string
  imported_at?: string | null
  meta: Record<string, string>
  items: BlueprintItem[]
  toml_content?: string
  error?: string
}

export interface BlueprintPendingResult {
  terminal_id: string
  status: 'pending'
  ready: false
  detail?: string
}

export type BlueprintPollResult = BlueprintResult | BlueprintPendingResult

export interface WorkGraphGenerationRequest {
  id: number
  terminal_id: string
  project_id: string
  description: string
  constraints: string
  persona: string
  provider: string
  mcp_capable: boolean
  base_blueprint_terminal_id?: string | null
  created_at: string
}

export interface Project {
  id: string
  root_directory: string | null
}

export interface Persona {
  name: string
  description: string
  default_provider: string
  source: string
  scope: string
  tags: string[]
}

export interface DashboardState {
  health: {
    status: string
    terminals: {
      total: number
      ready: number
      running: number
      completed: number
      error: number
    }
  }
  sessions: Session[]
  pending_prompt_count: number
  prompt_items: PromptItem[]
  pending_approvals: Approval[]
  approvals: Approval[]
  approvals_summary: {
    pending: number
    approved: number
    denied: number
    total: number
  }
  providers: ProviderHealth[]
  terminal_alerts: TerminalAlert[]
}

export interface TerminalAlert {
  id: string
  terminal_id: string
  session_name: string
  window_name: string
  severity: 'warning' | 'error'
  kind: 'idle' | 'error'
  message: string
  idle_streak_minutes?: number | null
}

export interface TerminalMetrics {
  terminal_id: string
  sampled_at: string
  output_velocity_tpm: number
  error_density: number
  idle_streak_minutes: number
  signal_counts: Record<string, number>
}

export interface EventLogItem {
  id: number
  terminal_id?: string | null
  type: string
  payload: Record<string, unknown>
  source_id?: string | null
  timestamp: string
}

export interface ContextPack {
  pack_id: string
  terminal_id?: string
  quality_score?: number
  token_budget?: number
  tokens_used?: number
  is_differential?: boolean
  base_pack_id?: string | null
  created_at?: string
  [key: string]: unknown
}

export interface Snapshot {
  id?: string
  snapshot_id?: string
  terminal_id?: string
  parent_id?: string | null
  event_cursor: number
  delta_event_count?: number
  summary_text: string
  derived_state?: Record<string, unknown>
  created_by?: string
  created_at?: string
  is_pinned?: boolean
  llm_prompt?: string
}

export interface SnapshotDiff {
  snapshot_a: string
  snapshot_b: string
  cursor_delta: number
  summary_a: string
  summary_b: string
  state_diff: Record<string, unknown>
}

export interface WorkItem {
  id: string
  project_id: string
  title: string
  description: string
  type: string
  status: string
  priority: number
  owner_terminal_id?: string | null
  acceptance_criteria: string[]
  files_of_interest: string[]
  proof_requirements?: string[] | null
  complexity: number
  created_at: string
  updated_at: string
}

export interface WorkEdge {
  id: number
  from_id: string
  to_id: string
  type: string
  created_by: string
  note?: string | null
  created_at: string
}

export interface WorkGraph {
  project_id: string
  work_items: WorkItem[]
  edges: WorkEdge[]
  critical_path: string[]
  scope_conflicts: Record<string, unknown>[]
}

export interface ProofWindow {
  id: number
  work_item_id: string
  opened_at?: string
  expires_at: string
  status: string
  proofs_collected?: Record<string, unknown>[]
  closed_at?: string | null
}

export interface TopologyProposal {
  id: string
  terminal_id: string
  proposal_type: string
  reason: string
  suggested_provider?: string | null
  suggested_persona?: string | null
  metrics_snapshot: Record<string, unknown>
  status: string
  created_at: string
  decided_at?: string | null
}

export interface ConversationTurn {
  type: 'human' | 'agent' | 'tool_call' | 'tool_result'
  content: string
  tool_name?: string
  index: number
}

export interface TerminalAnalysis {
  id: number
  terminal_id: string
  session_name?: string | null
  window_name?: string | null
  provider?: string | null
  tool_stats: Record<string, number>
  files_touched: string[]
  commands_run: string[]
  risk_flags: string[]
  work_item_id?: string | null
  compliance_summary?: {
    work_item_title: string
    files_of_interest: {
      total: number
      covered: number
      pct: number
      covered_files: string[]
      missed_files: string[]
    }
    acceptance_criteria: { criterion: string; evidence_found: boolean }[]
    has_risk_flags: boolean
  } | null
  line_count: number
  conversation_turn_count: number
  has_raw_log: boolean
  raw_log?: string
  match_excerpt?: string
  llm_review?: LLMReviewResult | null
  review_status?: 'pending' | 'running' | 'done' | 'skipped' | 'error'
  review_model?: string | null
  review_provider_id?: number | null
  review_error?: string | null
  reviewed_at?: string | null
  created_at: string
}

export interface LLMReviewResult {
  compliance_score: number
  verdict: string
  requirement_checks: {
    criterion: string
    status: 'met' | 'partial' | 'missed' | string
    evidence: string
    suggestion: string
  }[]
  deviation_summary: string
  work_item_improvements?: {
    description?: string
    add_criteria?: string[]
    clarify?: string[]
  }
  risk_assessment: 'low' | 'medium' | 'high' | string
  reviewer_notes?: string
}

export interface VerifierCheck {
  id: number
  run_id: number
  check_type: string
  name: string
  status: string
  command?: string | null
  exit_code?: number | null
  score?: number | null
  threshold?: number | null
  output_excerpt?: string | null
  artifact_ref?: string | null
  created_at: string
}

export interface VerifierRun {
  id: number
  work_item_id: string
  terminal_id?: string | null
  analysis_id?: number | null
  attempt_no: number
  trigger_source: string
  status: string
  strategy: Record<string, unknown>
  summary?: string | null
  failure_reason?: string | null
  raw_artifacts: Record<string, unknown>
  started_at?: string | null
  finished_at?: string | null
  created_at: string
  checks: VerifierCheck[]
}

export interface TerminalReview {
  terminal_id: string
  analysis_id?: number | null
  work_item_id?: string | null
  status: string
  review?: LLMReviewResult | null
  review_error?: string | null
  verifier_run?: VerifierRun | null
}

export interface LLMProviderConfig {
  id: number
  name: string
  provider_type: 'openai_compatible' | 'anthropic' | string
  base_url: string
  model: string
  is_active: boolean
  api_key_set: boolean
  created_at: string
  updated_at: string
}

export interface CapabilityEstimate {
  provider: string
  persona: string
  task_type: string
  alpha: number
  beta_param: number
  total_attempts: number
  mean: number
  last_updated?: string | null
}

export interface DirectoryEntry {
  name: string
  path: string
}

export interface DirectoryListing {
  path: string
  parent?: string | null
  children: DirectoryEntry[]
  shortcuts: {
    label: string
    path: string
  }[]
}

export interface SessionCreatePayload {
  provider: string
  agent_profile: string
  role: string
  working_directory?: string | null
  workers: {
    provider: string
    agent_profile: string
    role: string
    working_directory?: string | null
  }[]
}

export default {
  dashboardState: (silent = false) => api.get('/dashboard/state', {
    silent,
  } as any) as Promise<DashboardState>,
  personas: () => api.get('/personas') as Promise<Persona[]>,
  providers: () => api.get('/providers/health') as Promise<ProviderHealth[]>,
  directories: (path?: string) => api.get('/filesystem/directories', {
    params: { path: path || undefined },
  }) as Promise<DirectoryListing>,
  createDirectory: (path: string, name: string) =>
    api.post('/filesystem/directories', { path, name }) as Promise<DirectoryEntry>,
  createSession: (data: SessionCreatePayload) => api.post('/sessions', data) as Promise<Terminal>,
  createWorker: (sessionName: string, data: {
    provider: string
    agent_profile: string
    role: string
    working_directory?: string | null
  }) => api.post(`/sessions/${sessionName}/terminals`, data) as Promise<Terminal>,
  sendInput: (terminalId: string, data: {
    message: string
    requires_approval?: boolean
    supervisor_id?: string
    metadata_payload?: string | null
  }) => api.post(`/terminals/${terminalId}/input`, data),
  terminalOutput: (terminalId: string, silent = false) => api.get(`/terminals/${terminalId}/output`, {
    params: { mode: 'full' },
    silent,
  } as any) as Promise<{ output: string }>,
  events: (params?: {
    terminal_id?: string
    event_type?: string
    limit?: number
    since_cursor?: number
  }, silent = false) => api.get('/events', { params, silent } as any) as Promise<EventLogItem[]>,
  terminalMetrics: (terminalId: string, silent = false) => api.get(`/terminals/${terminalId}/metrics`, {
    silent,
  } as any) as Promise<TerminalMetrics>,
  buildContextPack: (terminalId: string, data: {
    query: string
    token_budget?: number
  }) => api.post(`/terminals/${terminalId}/context-pack`, data) as Promise<ContextPack>,
  latestContextPack: (terminalId: string, silent = false) => api.get(`/terminals/${terminalId}/context-pack/latest`, {
    silent,
  } as any) as Promise<ContextPack>,
  differentialContextPack: (terminalId: string, data: {
    base_pack_id: string
    query: string
  }) => api.post(`/terminals/${terminalId}/context-pack/differential`, data) as Promise<ContextPack>,
  compactionHistory: (terminalId?: string) => api.get('/compaction/history', {
    params: { terminal_id: terminalId || undefined },
  }) as Promise<Snapshot[]>,
  compactionDiff: (snapshotA: string, snapshotB: string) => api.get(`/compaction/diff/${snapshotA}/${snapshotB}`) as Promise<SnapshotDiff>,
  triggerCompaction: (terminalId: string) => api.post(`/terminals/${terminalId}/compaction/trigger`) as Promise<Snapshot>,
  latestSnapshot: (terminalId: string, silent = false) => api.get(`/terminals/${terminalId}/compaction/latest`, {
    silent,
  } as any) as Promise<Snapshot>,
  projects: () => api.get('/projects') as Promise<Project[]>,
  upsertProject: (id: string, rootDirectory: string | null) =>
    api.put(`/projects/${id}`, { root_directory: rootDirectory }) as Promise<Project>,
  deleteProject: (id: string) => api.delete(`/projects/${id}`) as Promise<void>,
  blueprintFile: (projectId: string, silent = false) => api.get(`/projects/${projectId}/blueprint-file`, {
    silent,
  } as any) as Promise<{
    found: boolean
    complete: boolean
    path: string | null
    item_count: number
    toml_content: string | null
  }>,
  workItems: (projectId: string, itemStatus?: string) => api.get(`/projects/${projectId}/work-items`, {
    params: { item_status: itemStatus || undefined },
  }) as Promise<WorkItem[]>,
  getWorkItem: (itemId: string) => api.get(`/work-items/${itemId}`) as Promise<WorkItem>,
  updateWorkItem: (itemId: string, data: Partial<Pick<WorkItem, 'title' | 'description' | 'status' | 'priority' | 'owner_terminal_id' | 'acceptance_criteria' | 'files_of_interest' | 'proof_requirements' | 'complexity'>>) => api.patch(`/work-items/${itemId}`, data) as Promise<WorkItem>,
  workGraph: (projectId: string) => api.get(`/projects/${projectId}/work-graph`) as Promise<WorkGraph>,
  proofWindows: (itemId: string) => api.get(`/work-items/${itemId}/proof-windows`) as Promise<ProofWindow[]>,
  createWorkEdge: (itemId: string, data: {
    from_id: string
    to_id: string
    type: string
    created_by: string
    note?: string | null
  }) => api.post(`/work-items/${itemId}/edges`, data) as Promise<WorkEdge>,
  deleteWorkEdge: (edgeId: number) => api.delete(`/work-edges/${edgeId}`),
  createWorkItem: (data: {
    project_id: string
    title: string
    description?: string
    type?: string
    priority?: number
    complexity?: number
    owner_terminal_id?: string | null
    acceptance_criteria?: string[]
    files_of_interest?: string[]
    proof_requirements?: string[] | null
  }) => api.post('/work-items', data) as Promise<WorkItem>,
  deleteWorkItem: (itemId: string) => api.delete(`/work-items/${itemId}`),
  topologyProposals: (status?: string) => api.get('/topology/proposals', {
    params: { status: status || undefined },
  }) as Promise<TopologyProposal[]>,
  acceptTopologyProposal: (proposalId: string) => api.post(`/topology/proposals/${proposalId}/accept`) as Promise<{ proposal_id: string, status: string }>,
  rejectTopologyProposal: (proposalId: string) => api.post(`/topology/proposals/${proposalId}/reject`) as Promise<{ proposal_id: string, status: string }>,
  capabilityEstimates: () => api.get('/capability-estimates') as Promise<CapabilityEstimate[]>,
  generateWorkGraph: (payload: {
    project_id: string
    description: string
    constraints?: string
    persona: string
    provider: string
    mcp_capable: boolean
    base_blueprint_terminal_id?: string | null
    language?: string
    root_directory?: string | null
  }) => api.post('/work-graph/generate', payload) as Promise<{ terminal_id: string; session_name: string }>,
  getBlueprint: (terminalId: string) => api.get(`/work-graph/blueprint/${terminalId}`) as Promise<BlueprintResult>,
  pollBlueprint: (terminalId: string) => api.get(`/work-graph/blueprint/${terminalId}`, {
    params: { pending_ok: true },
    silent: true,
  } as any) as Promise<BlueprintPollResult>,
  generationHistory: (projectId: string, limit = 20) => api.get(`/projects/${projectId}/work-graph/generation-requests`, {
    params: { limit },
  }) as Promise<WorkGraphGenerationRequest[]>,
  blueprintHistory: (projectId: string, limit = 20) => api.get(`/projects/${projectId}/work-graph/blueprints`, {
    params: { limit },
  }) as Promise<BlueprintResult[]>,
  importBlueprint: (terminalId: string, selectedIds?: string[]) =>
    api.post(`/work-graph/blueprint/${terminalId}/import`, { selected_item_ids: selectedIds ?? null }) as Promise<WorkItem[]>,
  deleteGenerationRequest: (requestId: number) => api.delete(`/work-graph/generation-requests/${requestId}`),
  deleteTerminal: (terminalId: string) => api.delete(`/terminals/${terminalId}`),
  deleteSession: (sessionName: string) => api.delete(`/sessions/${sessionName}`),
  terminalAnalysis: (terminalId: string) => api.get(`/terminals/${terminalId}/analysis`) as Promise<TerminalAnalysis>,
  terminalAnalyses: (limit = 50) => api.get('/terminal-analyses', { params: { limit } }) as Promise<TerminalAnalysis[]>,
  triggerAnalysis: (terminalId: string, runReview = false) => api.post(`/terminals/${terminalId}/analysis`, null, {
    params: { run_review: runReview || undefined },
  }) as Promise<TerminalAnalysis & { review?: TerminalReview | null }>,
  terminalReview: (terminalId: string) => api.get(`/terminals/${terminalId}/review`) as Promise<TerminalReview>,
  runTerminalReview: (terminalId: string, force = false) => api.post(`/terminals/${terminalId}/review`, {
    force,
    trigger_source: 'manual',
  }) as Promise<TerminalReview>,
  llmProviders: () => api.get('/llm-providers') as Promise<LLMProviderConfig[]>,
  createLlmProvider: (data: {
    name: string
    provider_type: string
    base_url: string
    api_key: string
    model: string
    is_active?: boolean
  }) => api.post('/llm-providers', data) as Promise<LLMProviderConfig>,
  updateLlmProvider: (id: number, data: Partial<{
    name: string
    provider_type: string
    base_url: string
    api_key: string
    model: string
    is_active: boolean
  }>) => api.put(`/llm-providers/${id}`, data) as Promise<LLMProviderConfig>,
  deleteLlmProvider: (id: number) => api.delete(`/llm-providers/${id}`),
  activateLlmProvider: (id: number) => api.post(`/llm-providers/${id}/activate`) as Promise<LLMProviderConfig>,
  testLlmProvider: (id: number) => api.post(`/llm-providers/${id}/test`, { prompt: 'hello' }) as Promise<{ ok: boolean; error?: string | null }>,
  verifierRuns: (workItemId: string) => api.get(`/work-items/${workItemId}/verifier-runs`) as Promise<VerifierRun[]>,
  terminalRawLog: (terminalId: string) => api.get(`/terminals/${terminalId}/raw-log`) as Promise<{ terminal_id: string; raw_log: string }>,
  terminalConversation: (terminalId: string) => api.get(`/terminals/${terminalId}/conversation`) as Promise<{ terminal_id: string; turns: ConversationTurn[] }>,
  searchTerminalAnalyses: (q: string, limit = 20) => api.get('/terminal-analyses/search', { params: { q, limit } }) as Promise<TerminalAnalysis[]>,
  approve: (id: number) => api.post(`/approvals/${id}/approve`) as Promise<Approval>,
  deny: (id: number, reason?: string) => api.post(`/approvals/${id}/deny`, {
    reason: reason || null,
  }) as Promise<Approval>,
}
