/** MATLAB→Python 项目工作台：复用项目静态分析，并展示 WCC 迁移过程。 */
import { useEffect, useMemo, useState } from 'react'
import { api } from './api'
import { ModelSettings } from '../../../shared/ModelSettings'
import type {
  Capabilities, ChainDetail, ChainNode, ChainState, ChainSummary, MigrationJob, MigrationEvent,
  ProjectAnalysis,
} from './types'

type WorkspaceTab = 'analysis' | 'migration'

const STATE_LABELS: Record<ChainState, string> = {
  pending: '等待', ready: '就绪', generated: '已生成', verified: '已检查',
  frozen: '已冻结', failed: '失败', manual_review: '人工复核',
}

const CAPABILITY_LABELS: Record<string, string> = {
  wcc_reason_act_observation: 'WCC Reason → Act → Observation',
  act_chunk_dag: 'ActChunk DAG · 失败二分',
  scc_atomic_boundaries: 'SCC 不可拆分边界',
  shared_project_static_analysis: '复用项目静态分析快照',
  optional_semantic_index: '可选三级语义索引',
  isolated_python_assembly: '隔离 Python 工程组装',
  syntax_and_import_validation: '语法与 import 静态检查',
  runnable_test_injection: '可注入运行测试',
  matlab_python_execution: 'MATLAB / Python 双端执行',
  numerical_differential_validation: '数值差分验证',
  checkpoint_resume: 'WCC 断点续跑',
  project_publication: '项目发布',
}

function StatusBadge({ state }: { state: ChainState }) {
  return <span className={`status-badge ${state}`}>{STATE_LABELS[state]}</span>
}

function EmptyPanel({ title, copy }: { title: string; copy: string }) {
  return <div className="empty-panel">
    <div className="empty-orbit"><span>W</span></div>
    <h2>{title}</h2><p>{copy}</p>
  </div>
}

function ChainCanvas({ detail }: { detail: ChainDetail }) {
  const positions = useMemo(() => new Map(detail.nodes.map((node, index) => [
    node.id, { x: 46 + (index % 3) * 230, y: 54 + Math.floor(index / 3) * 125 },
  ])), [detail.nodes])
  const rows = Math.max(1, Math.ceil(detail.nodes.length / 3))
  return <div className="chain-canvas" style={{ minHeight: `${rows * 125 + 75}px` }}>
    <svg className="edge-layer" aria-hidden="true">
      {detail.edges.map((edge) => {
        const source = positions.get(edge.source); const target = positions.get(edge.target)
        if (!source || !target) return null
        return <line key={`${edge.source}-${edge.target}`}
          x1={source.x + 76} y1={source.y + 28} x2={target.x + 76} y2={target.y + 28} />
      })}
    </svg>
    {detail.nodes.map((node) => {
      const position = positions.get(node.id)!
      return <div key={node.id} className={`chain-node ${node.kind} ${node.status}`}
        title={node.members.join('\n')}
        style={{ left: position.x, top: position.y }}>
        <small>{node.kind === 'entry' ? 'ENTRY' : node.kind.toUpperCase()}</small>
        <strong>{node.label}</strong>
        <span>{node.members.length} symbols</span>
      </div>
    })}
  </div>
}

function AnalysisView({ detail }: { detail: ChainDetail | null }) {
  if (!detail) return <EmptyPanel title="选择一个 WCC"
    copy="WCC 结构视图展示迁移任务中这条完整调用链的入口、SCC 组成、函数成员和可选三级语义。" />
  return <div className="analysis-grid">
    <section className="card structure-card">
      <div className="card-heading"><div><span>STRUCTURE</span><h2>调用链结构</h2></div>
        <b>{detail.summary.scc_count} SCC</b></div>
      <div className="entry-block"><span>主入口</span>
        <strong>{detail.summary.entry_symbols.join(', ') || '未识别'}</strong></div>
      <div className="member-list">
        {detail.nodes.map((node: ChainNode) => <div key={node.id}>
          <i className={node.kind} /><span>{node.label}</span><small>{node.members.length}</small>
        </div>)}
      </div>
    </section>
    <section className="card graph-card">
      <div className="card-heading"><div><span>CALL CHAIN</span><h2>WCC 调用关系</h2></div>
        <StatusBadge state={detail.summary.status} /></div>
      <ChainCanvas detail={detail} />
    </section>
    <section className="card semantics-card">
      <div className="card-heading"><div><span>SEMANTICS</span><h2>三级语义</h2></div></div>
      {detail.semantic_summary
        ? <p className="semantic-copy">{detail.semantic_summary}</p>
        : <div className="semantic-empty"><span>可选输入</span><h3>未加载 SemanticIndex</h3>
          <p>迁移仍会使用结构上下文执行。提供语义索引后，这里展示项目、文件与函数职责。</p></div>}
      <div className="metric-pair"><div><span>SYMBOLS</span><strong>{detail.summary.symbol_count}</strong></div>
        <div><span>ATTEMPTS</span><strong>{detail.summary.attempt_count}</strong></div></div>
    </section>
  </div>
}

function MigrationView({ detail }: { detail: ChainDetail | null }) {
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  useEffect(() => setSelectedFile(detail?.files[0]?.path || null), [detail?.summary.chain_id])
  if (!detail) return <EmptyPanel title="等待迁移任务"
    copy="启动任务后，这里会显示当前 WCC、Reason 策略、Observation 和生成文件。" />
  const file = detail.files.find((item) => item.path === selectedFile) || detail.files[0]
  return <div className="migration-layout">
    <div className="migration-main">
      <section className="card graph-card migration-graph">
        <div className="card-heading"><div><span>CURRENT WCC</span><h2>{detail.summary.chain_id}</h2></div>
          <StatusBadge state={detail.summary.status} /></div>
        <ChainCanvas detail={detail} />
      </section>
      <section className="card attempts-card">
        <div className="card-heading"><div><span>ATTEMPTS</span><h2>转换历史</h2></div>
          <b>{detail.attempts.length}</b></div>
        <div className="attempt-list">{detail.attempts.length ? detail.attempts.map((attempt) =>
          <div className={`attempt ${attempt.state}`} key={attempt.number}>
            <span>{String(attempt.number).padStart(2, '0')}</span><div><strong>{attempt.state}</strong><p>{attempt.summary}</p></div>
          </div>) : <p className="muted">尚未生成转换尝试</p>}</div>
        {detail.act_chunks.length > 0 && <>
          <h3 className="chunk-heading">ACT CHUNKS · {detail.act_chunks.length}</h3>
          <div className="chunk-list">{detail.act_chunks.map((chunk) =>
            <div className={`chunk-row ${chunk.status}`} key={chunk.chunk_id}>
              <div><strong>{chunk.chunk_id}</strong><small>{chunk.symbol_ids.join(', ')}</small></div>
              <span>{chunk.status} · F{chunk.function_count} · M{chunk.module_count}<br />
                in≈{chunk.input_tokens} / out≈{chunk.output_tokens}</span>
              {chunk.last_error && <p>{chunk.last_error}</p>}
            </div>)}</div>
        </>}
      </section>
      <section className="card files-card">
        <div className="file-tabs">{detail.files.map((item) =>
          <button className={item.path === file?.path ? 'active' : ''} key={item.path}
            onClick={() => setSelectedFile(item.path)}>{item.path.split('/').pop()}</button>)}</div>
        {file ? <><div className="file-path">{file.path}</div><pre><code>{file.content || '# 源码通过 artifact 按需加载'}</code></pre></>
          : <div className="file-empty">当前 WCC 尚无生成文件</div>}
      </section>
    </div>
    <aside className="reason-rail">
      <section className="rail-section">
        <div className="rail-title"><span>REASON</span><b>{detail.stratagem?.action || 'waiting'}</b></div>
        {detail.stratagem ? <>
          <p>{detail.stratagem.rationale || '已生成转换策略。'}</p>
          <h3>转换步骤</h3><ol>{detail.stratagem.conversion_steps.map((item) => <li key={item}>{item}</li>)}</ol>
          <h3>MATLAB 风险</h3><ul>{detail.stratagem.matlab_semantic_risks.map((item) => <li key={item}>{item}</li>)}</ul>
        </> : <p className="muted">等待 Context 构建完成</p>}
      </section>
      <section className="rail-section observation-section">
        <div className="rail-title"><span>OBSERVATION</span>
          <b className={detail.observation?.passed ? 'pass' : ''}>{detail.observation ? detail.observation.passed ? '已配置检查通过' : '发现问题' : 'waiting'}</b></div>
        <p className="muted">默认仅检查语法和相对 import；不代表运行成功或 MATLAB 数值等价。</p>
        <div className="fact-list">{detail.observation?.facts.map((fact, index) =>
          <div className={fact.passed ? 'fact pass' : 'fact fail'} key={`${fact.kind}-${index}`}>
            <i>{fact.passed ? '✓' : '!'}</i><div><strong>{fact.kind}</strong><p>{fact.detail || (fact.passed ? '检查通过' : '检查失败')}</p></div>
          </div>) || <p className="muted">尚无检查结果</p>}</div>
      </section>
    </aside>
  </div>
}

export default function App() {
  const [employeeId, setEmployeeId] = useState<string | null>(null)
  const [administrator, setAdministrator] = useState(false)
  const [loginInput, setLoginInput] = useState('')
  const [sessionChecked, setSessionChecked] = useState(false)
  const [tab, setTab] = useState<WorkspaceTab>('migration')
  const [analyses, setAnalyses] = useState<ProjectAnalysis[]>([])
  const [analysisJobId, setAnalysisJobId] = useState('')
  const [online, setOnline] = useState<boolean | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [job, setJob] = useState<MigrationJob | null>(null)
  const [chains, setChains] = useState<ChainSummary[]>([])
  const [selectedChain, setSelectedChain] = useState<string | null>(null)
  const [detail, setDetail] = useState<ChainDetail | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [history, setHistory] = useState<MigrationJob[]>([])
  const [jobId, setJobId] = useState<string | null>(null)
  const [events, setEvents] = useState<MigrationEvent[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [clock, setClock] = useState(Date.now())

  const backendReady = capabilities?.status === 'ready'
  const selectedAnalysis = analyses.find((item) => item.analysis_job_id === analysisJobId)
  const busy = job?.state === 'queued' || job?.state === 'running'
  const progress = Math.max(0, Math.min(100, Math.round((job?.progress || 0) * 100)))
  const heartbeat = job?.heartbeat
  const heartbeatElapsed = heartbeat
    ? Math.max(heartbeat.elapsed_ms, clock - Date.parse(heartbeat.started_at))
    : 0

  useEffect(() => {
    api.session()
      .then((session) => {
        setEmployeeId(session.employee_id)
        setAdministrator(session.administrator)
      })
      .catch(() => setEmployeeId(null))
      .finally(() => setSessionChecked(true))
  }, [])

  useEffect(() => {
    if (!employeeId) return
    let cancelled = false
    Promise.all([api.health(), api.capabilities(), api.projects(), api.projectAnalyses()])
      .then(([, value, projects, availableAnalyses]) => {
        if (cancelled) return
        setOnline(true); setCapabilities(value); setHistory(projects); setAnalyses(availableAnalyses)
        const firstUsable = availableAnalyses.find((item) => item.usable)
        if (firstUsable) setAnalysisJobId(firstUsable.analysis_job_id)
        if (projects[0]) setJobId(projects[0].job_id)
      })
      .catch(() => { if (!cancelled) setOnline(false) })
    return () => { cancelled = true }
  }, [employeeId])

  useEffect(() => {
    if (!busy) return
    const timer = window.setInterval(() => setClock(Date.now()), 500)
    return () => window.clearInterval(timer)
  }, [busy])

  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    let timer: number | undefined
    setSelectedChain(null); setDetail(null); setChains([]); setEvents([]); setJob(null)
    async function refresh() {
      try {
        const [current, items, log] = await Promise.all([
          api.migration(jobId!), api.chains(jobId!), api.events(jobId!),
        ])
        if (cancelled) return
        setOnline(true); setJob(current); setChains(items); setEvents(log)
        setSelectedChain((previous) => items.some((item) => item.chain_id === previous)
          ? previous : current.active_chain_id || items[0]?.chain_id || null)
        if (current.state !== 'running' && current.state !== 'queued') {
          const projects = await api.projects()
          if (!cancelled) setHistory(projects)
          return
        }
      } catch (error) { if (!cancelled) { setNotice((error as Error).message); setOnline(false) } }
      if (!cancelled) timer = window.setTimeout(refresh, 1600)
    }
    void refresh()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [jobId, submitting])

  useEffect(() => {
    if (!jobId) return
    const source = new EventSource(`/api/migrations/${encodeURIComponent(jobId)}/heartbeat`)
    source.onmessage = (event) => {
      try {
        const current = JSON.parse(event.data) as MigrationJob
        setOnline(true); setJob(current); setClock(Date.now())
        if (current.state !== 'running' && current.state !== 'queued') source.close()
      } catch { /* 非法单条事件交给常规状态轮询恢复。 */ }
    }
    source.onerror = () => source.close()
    return () => source.close()
  }, [jobId, submitting])

  useEffect(() => {
    let cancelled = false
    if (!job || !selectedChain || job.job_id !== jobId) return
    api.chain(job.job_id, selectedChain)
      .then((value) => { if (!cancelled) setDetail(value) })
      .catch((error) => { if (!cancelled) setNotice((error as Error).message) })
    return () => { cancelled = true }
  }, [job, jobId, selectedChain])

  async function startMigration() {
    if (!analysisJobId || !selectedAnalysis?.usable) {
      setNotice('请先在 5173 完成项目静态分析，并在这里选择可复用快照'); return
    }
    try {
      setNotice(null); setSubmitting(true)
      const created = await api.createMigration(analysisJobId)
      setJobId(created.job_id); setHistory((items) => [created, ...items]); setTab('migration')
    } catch (error) { setNotice((error as Error).message) }
    finally { setSubmitting(false) }
  }

  async function resumeMigration() {
    if (!job) return
    try {
      setNotice(null); setSubmitting(true)
      await api.resume(job.job_id)
      setTab('migration')
    }
    catch (error) { setNotice((error as Error).message) }
    finally { setSubmitting(false) }
  }

  async function restartMigration() {
    if (!job) return
    try {
      setNotice(null); setSubmitting(true)
      const created = await api.restart(job.job_id)
      setJobId(created.job_id); setHistory((items) => [created, ...items]); setTab('migration')
    } catch (error) { setNotice((error as Error).message) }
    finally { setSubmitting(false) }
  }

  async function copyCli() {
    const command = `matlab-refactor migrate "${selectedAnalysis?.project_path || 'D:\\path\\to\\matlab-project'}"`
    try { await navigator.clipboard.writeText(command); setNotice('CLI 命令已复制；CLI 是独立入口，会自行执行项目静态分析') }
    catch { setNotice('无法访问剪贴板，请参照使用教程复制命令') }
  }

  async function login() {
    try {
      const session = await api.login(loginInput.trim())
      setEmployeeId(session.employee_id); setAdministrator(session.administrator); setNotice(null)
    } catch (error) { setNotice((error as Error).message) }
  }

  async function downloadResult() {
    if (!job) return
    try {
      await api.exportJob(job.job_id)
      window.location.assign(`/api/migrations/${encodeURIComponent(job.job_id)}/download`)
    } catch (error) { setNotice((error as Error).message) }
  }

  if (!sessionChecked) return <main className="login-screen"><div className="login-card">正在连接服务器…</div></main>
  if (!employeeId) return <main className="login-screen"><form className="login-card" onSubmit={(event) => { event.preventDefault(); void login() }}>
    <div className="brand-mark">M<span>→</span>P</div><h1>Migration Workbench</h1>
    <p>请输入工号。首次使用会自动创建个人数据空间。</p>
    <input autoFocus aria-label="工号" value={loginInput} onChange={(event) => setLoginInput(event.target.value)} placeholder="例如：E001" />
    <button className="run-button" disabled={!loginInput.trim()}>进入工作台</button>
    {notice && <span className="login-error">{notice}</span>}
  </form></main>

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand-mark">M<span>→</span>P</div>
      <div className="brand-copy"><strong>Migration Workbench</strong><span>MATLAB → Python · WCC Agent</span></div>
      <nav className="tabs">
        <button className={tab === 'analysis' ? 'active' : ''} onClick={() => setTab('analysis')}>WCC 结构</button>
        <button className={tab === 'migration' ? 'active' : ''} onClick={() => setTab('migration')}>迁移</button>
      </nav>
      <div className="migration-top-controls"><button className="settings-toggle" disabled={!job} onClick={downloadResult}>下载结果</button>{administrator && <button className="settings-toggle" onClick={() => setSettingsOpen(true)}>设置</button>}
        <div className={`service ${online ? 'online' : online === false ? 'offline' : ''}`}><i />
          {employeeId} · {online === null ? '连接中' : online ? '已连接' : '离线'}</div></div>
    </header>

    <section className="command-bar">
      <label className="analysis-picker"><span>PROJECT STATIC ANALYSIS · 5173</span>
        <select aria-label="项目静态分析" value={analysisJobId}
          onChange={(event) => { setAnalysisJobId(event.target.value); setNotice(null) }}>
          <option value="">选择 5173 已完成的项目静态分析</option>
          {analyses.map((item) => <option key={item.analysis_job_id} value={item.analysis_job_id} disabled={!item.usable}>
            {item.usable ? '可用' : '需刷新'} · {item.project_path} · {item.semantics_ready ? '含三级语义' : '仅结构'}
          </option>)}
        </select>
        <small>{selectedAnalysis
          ? selectedAnalysis.usable
            ? '迁移将直接复用扫描、解析和调用图；不会重复执行 Scanner / Parser / Analyzer。'
            : selectedAnalysis.unavailable_reason || '这个快照不可复用，请回到 5173 刷新。'
          : '先在 5173 执行“项目静态分析”；这里会自动读取同一份本地快照。'}</small>
      </label>
      <button className="cli-button" onClick={copyCli}>复制 CLI</button>
      <button className="run-button" disabled={!online || !backendReady || busy || submitting || !selectedAnalysis?.usable} onClick={startMigration}>
        {submitting ? '提交中…' : busy ? '迁移中…' : '开始迁移'}
      </button>
    </section>

    <section className="history-bar">
      <label>历史任务 <select aria-label="历史任务" disabled={submitting} value={jobId || ''} onChange={(event) => {
        setJobId(event.target.value || null); setNotice(null)
      }}><option value="" disabled>选择任务</option>{history.map((item) =>
        <option key={item.job_id} value={item.job_id}>{item.job_id} · {item.state} · {item.project_path}</option>
      )}</select></label>
      <button onClick={async () => {
        try {
          const [items, value, availableAnalyses] = await Promise.all([api.projects(), api.capabilities(), api.projectAnalyses()])
          setHistory(items); setCapabilities(value); setAnalyses(availableAnalyses); setOnline(true); setNotice(null)
          if (!analysisJobId) setAnalysisJobId(availableAnalyses.find((item) => item.usable)?.analysis_job_id || '')
        }
        catch (error) { setOnline(false); setNotice((error as Error).message) }
      }}>刷新连接 / 历史</button>
      <button className="resume-button" disabled={!online || !job?.can_resume || busy || submitting} onClick={resumeMigration}>断点续跑</button>
      <button className="resume-button" disabled={!online || !job || busy || submitting} onClick={restartMigration}>完全重跑（重建结构图）</button>
      <span>已冻结 {job?.completed_chains || 0} / {job?.total_chains || 0} · {progress}%</span>
    </section>

    <section className="status-bar">
      <div><span>JOB</span><strong>{job?.job_id || '—'}</strong></div>
      <div><span>STATE</span><strong>{job?.state || capabilities?.status || 'unknown'}</strong></div>
      <div><span>STATIC ANALYSIS</span><strong>{job?.static_analysis_reused ? 'reused' : 'direct'}</strong></div>
      <div><span>SEMANTICS</span><strong>{job?.semantic_index_used ? 'loaded' : 'optional'}</strong></div>
      <div className="progress"><span style={{ width: `${progress}%` }} /></div>
    </section>
    {notice && <div role="alert" className="notice">{notice}<button onClick={() => setNotice(null)}>关闭</button></div>}
    {job && <div className={`live-heartbeat ${heartbeat?.phase || job.state}`} role="status" aria-live="polite">
      <i /><strong>实时心跳</strong><code>{heartbeat?.chunk_id || heartbeat?.chain_id || 'project'}</code>
      <span>{heartbeat
        ? `${heartbeat.tool} · ${heartbeat.phase} · ${(heartbeatElapsed / 1000).toFixed(1)}s · reasoning ${heartbeat.reasoning_chars} · content ${heartbeat.content_chars}`
        : job.message}</span>
      {heartbeat?.first_token_ms != null && <small>首 token {heartbeat.first_token_ms}ms</small>}
      {heartbeat?.finish_reason && <small>finish {heartbeat.finish_reason}</small>}
    </div>}
    {job && <details className="run-log">
      <summary>{job.message} <span>· 展开最近 100 条日志</span></summary>
      <ol>{events.slice(-100).map((event) => <li key={event.sequence}>
        <time>{new Date(event.time).toLocaleTimeString('zh-CN', { hour12: false })}</time>
        <code>{event.chain_id || 'project'}</code><span>{event.message}</span>
      </li>)}</ol>
      {!events.length && <p>正在等待读取项目静态分析结果。</p>}
      <p>产物目录：{job.output_directory}</p>
      {job.error && <p className="error-copy">{job.error}</p>}
    </details>}
    {job?.project_observation && <details className="run-log" open={!job.project_observation.passed}>
      <summary>项目级检查：{job.project_observation.passed ? '通过' : '存在阻断问题'}</summary>
      {job.project_observation.facts
        .filter((fact) => !fact.passed || fact.kind === 'manual_review')
        .map((fact, index) => <div className={fact.passed ? 'fact pass' : 'fact fail'} key={`${fact.kind}-${index}`}>
          <i>{fact.passed ? '✓' : '!'}</i><div><strong>{fact.kind}</strong>
            <p>{fact.detail || (fact.passed ? '待人工复核，不阻断发布' : '阻断发布')}</p>
            {fact.actual != null && <pre>{JSON.stringify(fact.actual, null, 2)}</pre>}
          </div>
        </div>)}
    </details>}

    <div className="workbench">
      <aside className="chain-queue">
        <div className="queue-heading"><div><span>CALL CHAINS</span><h2>WCC 队列</h2></div><b>{chains.length}</b></div>
        <div className="queue-list">{chains.map((chain, index) =>
          <button key={chain.chain_id} className={selectedChain === chain.chain_id ? 'selected' : ''}
            onClick={() => {
              if (selectedChain !== chain.chain_id) { setSelectedChain(chain.chain_id); setDetail(null) }
            }}>
            <span className="chain-index">{String(index + 1).padStart(2, '0')}</span>
            <div><strong>{chain.entry_symbols.join(', ') || chain.chain_id}</strong>
              <small>{chain.symbol_count} symbols · {chain.scc_count} SCC</small></div>
            <StatusBadge state={chain.status} />
          </button>)}
          {!chains.length && <div className="queue-empty"><span>暂无 WCC</span><p>先选择 5173 的项目静态分析并开始迁移；迁移读取快照后会显示调用链。</p></div>}
        </div>
        <div className="capability-foot"><span>AGENT CAPABILITIES</span>
          {(capabilities?.implemented || []).slice(0, 4).map((item) =>
            <p key={item}>✓ {CAPABILITY_LABELS[item] || item}</p>)}</div>
      </aside>
      <main className="workspace-main">
        {tab === 'analysis' ? <AnalysisView detail={detail} /> : <MigrationView detail={detail} />}
      </main>
    </div>
    {settingsOpen && <ModelSettings onClose={() => setSettingsOpen(false)} />}
  </div>
}
