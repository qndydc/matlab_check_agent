/**
 * Description: Web MVP 与后端交换数据时使用的 TypeScript 类型契约。
 * 作用: 描述 Job、图节点、图边以及函数/文件/项目三级注释的数据结构。
 * 优点: 编译期即可发现字段拼写和类型错误，并为编辑器提供自动补全。
 * 调用/被调用: 被 api.ts 和 App.tsx 引用；字段与后端 Pydantic 模型的 JSON 输出对应。
 * 基础知识: interface 描述对象形状，type 适合表达字符串联合类型；它们只参与类型检查，不进入最终 JavaScript。
 */
export type JobState = 'queued' | 'running' | 'completed' | 'failed'
export type JobStage = 'analyze' | 'annotate'

export interface JobStatus {
  job_id: string
  project_path: string
  state: JobState
  stage: JobStage
  message: string
  error: string | null
  graph_ready: boolean
  semantics_ready: boolean
  analysis_ready: boolean
  can_resume: boolean
  created_at: string
  updated_at: string
}

export interface GraphNodeData {
  id: string
  label: string
  qualified_name: string
  kind: string
  file_path: string
  start_line: number
  end_line: number
  inputs: string[]
  outputs: string[]
  is_entry_point: boolean
  is_core: boolean
  is_orphan: boolean
}

export interface GraphDocument {
  schema_version: string
  project_root: string
  nodes: GraphNodeData[]
  edges: { id: string; source: string; target: string; is_cycle: boolean }[]
  entry_points: string[]
  cycles: string[][]
  unresolved_calls: Record<string, string[]>
}

export type GraphScope = 'project' | 'directory' | 'file' | 'function'
export type GraphDirection = 'both' | 'callers' | 'callees'

export interface GraphViewNode extends Partial<GraphNodeData> {
  id: string
  node_type: 'directory' | 'file' | 'function'
  label: string
  qualified_name: string
  file_path: string
  member_count: number
  internal_call_count: number
  cycle_count: number
  has_children: boolean
  semantic_summary: string | null
}

export interface GraphBreadcrumb {
  label: string
  scope: GraphScope
  focus_id: string | null
}

export interface GraphViewDocument {
  schema_version: string
  scope: GraphScope
  focus_id: string | null
  nodes: GraphViewNode[]
  edges: { id: string; source: string; target: string; weight: number; is_cycle: boolean }[]
  breadcrumbs: GraphBreadcrumb[]
  total_nodes: number
  visible_nodes: number
  truncated: boolean
}

export interface GraphViewRequest {
  scope: GraphScope
  focusId?: string | null
  depth?: number
  limit?: number
  direction?: GraphDirection
  query?: string
}

export interface FunctionAnnotation {
  symbol_id: string
  file_path: string
  start_line: number
  end_line: number
  summary: string
  inputs: string[]
  outputs: string[]
  data_flow: string[]
  side_effects: string[]
  risks: string[]
  open_questions: string[]
  confidence: number
}

export interface FunctionSource {
  symbol_id: string
  file_path: string
  start_line: number
  end_line: number
  source: string
}

export type SemanticNode = 'initialize' | 'select_unit' | 'annotate' | 'quality_check' | 'aggregate'
export type SemanticEventPhase = 'started' | 'completed' | 'retrying' | 'manual_review' | 'failed'
export type SemanticQualityStatus = 'accepted' | 'low_quality' | 'incomplete' | 'manual_review'

/** LangGraph 语义循环向外部监控端发布的一条有序事件。 */
export interface SemanticProgressEvent {
  job_id: string
  sequence: number
  node: SemanticNode
  phase: SemanticEventPhase
  message: string
  unit_id: string | null
  attempt: number | null
  quality_status: SemanticQualityStatus | null
  details: Record<string, unknown>
  created_at: string
}

/** SSE 流结束时携带的任务终态，用于让页面停止等待实时事件。 */
export interface SemanticStreamTerminal {
  job_id: string
  state: 'completed' | 'failed'
  message: string
  error: string | null
}

export interface FileAnnotation {
  file_path: string
  role: string
  function_symbols: string[]
  risks: string[]
  confidence: number
}

export interface SemanticIndex {
  schema_version: string
  project_root: string
  functions: FunctionAnnotation[]
  files: FileAnnotation[]
  project: {
    purpose: string
    usage: string
    entry_points: string[]
    files: string[]
    risks: string[]
    confidence: number
  }
  conflicts: { symbol_id: string; reason: string }[]
}
