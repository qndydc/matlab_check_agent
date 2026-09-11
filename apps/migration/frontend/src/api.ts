/** 迁移工作台 API 客户端；所有错误统一转换成用户可读消息。 */

import type {
  Capabilities, ChainDetail, ChainSummary, EmployeeSession, MigrationJob, MigrationEvent, ProjectAnalysis,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(typeof payload?.detail === 'string' ? payload.detail : `请求失败：HTTP ${response.status}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string; application: string }>('/api/health'),
  session: () => request<EmployeeSession>('/api/session'),
  login: (employeeId: string) => request<EmployeeSession>('/api/session', {
    method: 'POST', body: JSON.stringify({ employee_id: employeeId }),
  }),
  logout: () => request<void>('/api/session', { method: 'DELETE' }),
  capabilities: () => request<Capabilities>('/api/capabilities'),
  projects: () => request<MigrationJob[]>('/api/migrations'),
  projectAnalyses: () => request<ProjectAnalysis[]>('/api/project-analyses'),
  resume: (jobId: string) => request<MigrationJob>(`/api/migrations/${encodeURIComponent(jobId)}/resume`, { method: 'POST' }),
  restart: (jobId: string) => request<MigrationJob>(`/api/migrations/${encodeURIComponent(jobId)}/restart`, { method: 'POST' }),
  events: (jobId: string) => request<MigrationEvent[]>(`/api/migrations/${encodeURIComponent(jobId)}/events`),
  createMigration: (analysisJobId: string) =>
    request<MigrationJob>('/api/migrations', {
      method: 'POST',
      body: JSON.stringify({ analysis_job_id: analysisJobId }),
    }),
  migration: (jobId: string) => request<MigrationJob>(`/api/migrations/${encodeURIComponent(jobId)}`),
  chains: (jobId: string) => request<ChainSummary[]>(`/api/migrations/${encodeURIComponent(jobId)}/chains`),
  chain: (jobId: string, chainId: string) =>
    request<ChainDetail>(`/api/migrations/${encodeURIComponent(jobId)}/chains/${encodeURIComponent(chainId)}`),
  exportJob: (jobId: string) => request<{ download_url: string; filename: string }>(
    `/api/migrations/${encodeURIComponent(jobId)}/export`, { method: 'POST' },
  ),
}
