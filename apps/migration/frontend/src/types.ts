/** 迁移前端与后端交换的最小类型；大型源码和日志继续使用 artifact 引用。 */

export type MigrationState =
  | 'queued' | 'running' | 'completed' | 'failed' | 'manual_review' | 'interrupted'

export type ChainState =
  | 'pending' | 'ready' | 'generated' | 'verified' | 'frozen'
  | 'failed' | 'manual_review'

export type Capabilities = {
  application: string
  version: string
  status: string
  implemented: string[]
  pending: string[]
}

export type EmployeeSession = {
  employee_id: string
  administrator: boolean
}

export type MigrationJob = {
  job_id: string
  project_path: string
  analysis_job_id: string | null
  state: MigrationState
  progress: number
  active_chain_id: string | null
  semantic_index_used: boolean
  static_analysis_reused: boolean
  error: string | null
  message: string
  can_resume: boolean
  completed_chains: number
  total_chains: number
  output_directory: string
  created_at: string
  heartbeat: MigrationHeartbeat | null
  project_observation: ProjectObservation | null
}

export type MigrationHeartbeat = {
  updated_at: string
  started_at: string
  chain_id: string | null
  chunk_id?: string
  tool: string
  phase: 'started' | 'streaming' | 'completed' | 'failed'
  attempt: number
  elapsed_ms: number
  first_token_ms: number | null
  reasoning_chars: number
  content_chars: number
  chunks: number
  finish_reason: string | null
  error_type?: string
  message: string
}

/** 5173 保存的项目静态分析；5174 仅引用它，不重复 Scanner / Parser / Analyzer。 */
export type ProjectAnalysis = {
  analysis_job_id: string
  project_path: string
  semantics_ready: boolean
  updated_at: string
  message: string
  usable: boolean
  unavailable_reason: string | null
}

export type MigrationEvent = {
  sequence: number
  time: string
  stage: string
  chain_id: string | null
  message: string
}

export type ChainSummary = {
  chain_id: string
  entry_symbols: string[]
  symbol_count: number
  scc_count: number
  status: ChainState
  attempt_count: number
}

export type ChainNode = {
  id: string
  label: string
  kind: 'entry' | 'scc' | 'function'
  status: ChainState
  members: string[]
}

export type ChainEdge = { source: string; target: string }

export type StratagemView = {
  action: 'convert' | 'finish' | 'rebuild_context' | 'replan' | 'manual_review'
  rationale: string
  conversion_steps: string[]
  matlab_semantic_risks: string[]
  validation_plan: string[]
}

export type ObservationFact = {
  kind: string
  passed: boolean
  detail: string
  actual?: unknown
}

export type ProjectObservation = {
  passed: boolean
  facts: ObservationFact[]
}

export type AttemptView = {
  number: number
  state: 'generated' | 'observed' | 'failed' | 'accepted'
  summary: string
}

export type GeneratedFile = {
  path: string
  language: string
  content?: string
}

export type ActChunkView = {
  chunk_id: string
  symbol_ids: string[]
  depends_on_chunks: string[]
  input_tokens: number
  output_tokens: number
  function_count: number
  module_count: number
  status: 'pending' | 'running' | 'frozen' | 'failed' | 'superseded'
  attempts: number
  parent_chunk_id: string | null
  last_error: string | null
}

export type ChainDetail = {
  summary: ChainSummary
  nodes: ChainNode[]
  edges: ChainEdge[]
  stratagem: StratagemView | null
  observation: { passed: boolean; facts: ObservationFact[] } | null
  attempts: AttemptView[]
  act_chunks: ActChunkView[]
  files: GeneratedFile[]
  semantic_summary: string | null
}
