/**
 * Description: 前端访问本地 FastAPI 服务的统一请求模块。
 * 作用: 封装健康检查、任务提交、状态查询以及图和语义结果读取。
 * 优点: 页面组件不需要重复处理 URL、JSON 请求头和 HTTP 错误。
 * 调用/被调用: 被 App.tsx 调用；调用浏览器 fetch API，并使用 types.ts 中的接口定义返回值。
 * 基础知识: fetch 返回 Promise；泛型 T 用来告诉 TypeScript 每个接口成功响应的数据形状。
 */
import type { GraphDocument, GraphViewDocument, GraphViewRequest, JobStatus, SemanticIndex } from './types'

/** 发送 JSON HTTP 请求，并将非成功响应转换成可读的 Error。 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
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
  /** 提交一个新的普通项目分析任务。 */
  analyze: (projectPath: string) =>
    request<JobStatus>('/api/jobs/analyze', {
      method: 'POST',
      body: JSON.stringify({ project_path: projectPath }),
    }),
  /** 在已有图任务上启动三级语义注释。 */
  annotate: (jobId: string) =>
    request<JobStatus>(`/api/jobs/${jobId}/annotate`, { method: 'POST' }),
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
  /** 列出已经保存到本地 SQLite 的项目。 */
  projects: () => request<JobStatus[]>('/api/projects'),
  /** 删除一个本地项目及其保存的图和三级语义。 */
  deleteProject: (jobId: string) =>
    request<void>(`/api/projects/${jobId}`, { method: 'DELETE' }),
}
