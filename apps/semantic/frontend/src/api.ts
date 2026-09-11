/**
 * Description: 前端访问本地 FastAPI 服务的统一请求模块。
 * 作用: 封装健康检查、任务提交、状态查询以及图和语义结果读取。
 * 优点: 页面组件不需要重复处理 URL、JSON 请求头和 HTTP 错误。
 * 调用/被调用: 被 App.tsx 调用；调用浏览器 fetch API，并使用 types.ts 中的接口定义返回值。
 * 基础知识: fetch 返回 Promise；泛型 T 用来告诉 TypeScript 每个接口成功响应的数据形状。
 */
import type {
  FunctionSource, GraphDocument, GraphViewDocument, GraphViewRequest, JobStatus,
  EmployeeSession, SemanticIndex, SemanticProgressEvent, SemanticStreamTerminal,
  SourceProject,
} from './types'

/** 发送 JSON HTTP 请求，并将非成功响应转换成可读的 Error。 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    cache: 'no-store',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(payload?.detail || `请求失败 (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  /** 检查本地后端服务是否可以访问。 */
  health: () => request<{ status: string }>('/api/health'),
  session: () => request<EmployeeSession>('/api/session'),
  login: (employeeId: string) => request<EmployeeSession>('/api/session', {
    method: 'POST', body: JSON.stringify({ employee_id: employeeId }),
  }),
  logout: () => request<void>('/api/session', { method: 'DELETE' }),
  /** 提交一个新的普通项目分析任务。 */
  analyze: (projectId: string) =>
    request<JobStatus>('/api/jobs/analyze', {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId }),
    }),
  sourceProjects: () => request<SourceProject[]>('/api/source-projects'),
  uploadProject: (file: File, onProgress?: (percent: number) => void) =>
    new Promise<SourceProject>((resolve, reject) => {
      const form = new FormData()
      form.append('file', file)
      const xhr = new XMLHttpRequest()
      xhr.open('POST', '/api/source-projects/upload')
      xhr.withCredentials = true
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress?.(Math.round(event.loaded / event.total * 100))
      }
      xhr.onload = () => {
        let payload: any = null
        try { payload = JSON.parse(xhr.responseText) } catch { /* handled below */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(payload as SourceProject)
        else reject(new Error(payload?.detail || `上传失败 (${xhr.status})`))
      }
      xhr.onerror = () => reject(new Error('上传连接中断'))
      xhr.send(form)
    }),
  deleteSourceProject: (projectId: string) =>
    request<void>(`/api/source-projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' }),
  exportJob: (jobId: string) => request<{ download_url: string }>(
    `/api/jobs/${encodeURIComponent(jobId)}/export`, { method: 'POST' },
  ),
  /** 在已有图任务上启动三级语义注释。 */
  annotate: (jobId: string) =>
    request<JobStatus>(`/api/jobs/${jobId}/annotate`, { method: 'POST' }),
  resume: (jobId: string) => request<JobStatus>(`/api/jobs/${jobId}/resume`, { method: 'POST' }),
  restart: (jobId: string) => request<JobStatus>(`/api/jobs/${jobId}/restart`, { method: 'POST' }),
  /** 查询指定任务的当前阶段、状态和结果就绪标记。 */
  job: (jobId: string) => request<JobStatus>(`/api/jobs/${jobId}`),
  /** 获取指定任务生成的调用图文档。 */
  graph: (jobId: string) => request<GraphDocument>(`/api/jobs/${jobId}/graph`),
  /** 获取按目录、文件或函数焦点裁剪后的中央画布视图。 */
  graphView: (jobId: string, options: GraphViewRequest) => {
    const params = new URLSearchParams({
      scope: options.scope,
      depth: String(options.depth ?? 1),
      limit: String(options.limit ?? 80),
      direction: options.direction ?? 'both',
    })
    if (options.focusId) params.set('focus_id', options.focusId)
    if (options.query?.trim()) params.set('query', options.query.trim())
    return request<GraphViewDocument>(`/api/jobs/${jobId}/graph/view?${params}`)
  },
  /** 获取指定任务生成的三级语义索引。 */
  semantics: (jobId: string) =>
    request<SemanticIndex>(`/api/jobs/${jobId}/semantics`),
  /** 增量读取已持久化的语义循环事件，用于首次加载和 SSE 断线补偿。 */
  semanticEvents: (jobId: string, afterSequence = 0) =>
    request<SemanticProgressEvent[]>(`/api/jobs/${jobId}/semantic-events?after_sequence=${afterSequence}`),
  /** 订阅实时语义进度；返回关闭函数，供 React effect 切换任务时清理连接。 */
  semanticEventStream: (
    jobId: string,
    afterSequence: number,
    handlers: {
      onOpen: () => void
      onEvent: (event: SemanticProgressEvent) => void
      onTerminal: (terminal: SemanticStreamTerminal) => void
      onError: () => void
    },
  ) => {
    const source = new EventSource(`/api/jobs/${jobId}/semantic-events/stream?after_sequence=${afterSequence}`)
    source.onopen = handlers.onOpen
    source.onerror = handlers.onError
    source.addEventListener('semantic_progress', (message) => {
      handlers.onEvent(JSON.parse((message as MessageEvent<string>).data) as SemanticProgressEvent)
    })
    source.addEventListener('terminal', (message) => {
      handlers.onTerminal(JSON.parse((message as MessageEvent<string>).data) as SemanticStreamTerminal)
      source.close()
    })
    return () => source.close()
  },
  /** 按图中的函数标识读取对应源码切片。 */
  functionSource: (jobId: string, symbolId: string) =>
    request<FunctionSource>(`/api/jobs/${jobId}/functions/${encodeURIComponent(symbolId)}/source`),
  /** 列出已经保存到本地 SQLite 的项目。 */
  projects: () => request<JobStatus[]>('/api/projects'),
  /** 删除一个本地项目及其保存的图和三级语义。 */
  deleteProject: (jobId: string) =>
    request<void>(`/api/projects/${jobId}`, { method: 'DELETE' }),
}
