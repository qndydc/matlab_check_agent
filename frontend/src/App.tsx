/**
 * Description: MATLAB Atlas 前端的主页面与核心交互组件。
 * 作用: 组织项目路径输入、任务轮询、文件树、调用图和三级语义详情。
 * 优点: 将 MVP 的主要状态集中管理，让树、图和详情面板共享同一份选择状态。
 * 调用/被调用: 被 main.tsx 渲染；调用 api.ts、types.ts、React Flow、Dagre 和多个页面内展示组件。
 * 基础知识: React 组件是返回 JSX 的函数；useState 保存界面状态，useEffect 处理副作用，useMemo/useCallback 缓存计算或函数。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Background, Controls, MarkerType, MiniMap, ReactFlow,
  type Edge, type Node,
} from '@xyflow/react'
import dagre from 'dagre'
import {
  AlertTriangle, ArrowDownToLine, ArrowUpFromLine, Braces, ChevronDown, ChevronRight, CircleDot,
  Database, FileCode2, FolderTree, History, LoaderCircle, Network, Play,
  RotateCcw, Search, Sparkles, Trash2, X,
} from 'lucide-react'
import { api } from './api'
import type {
  FileAnnotation, FunctionAnnotation, GraphDirection, GraphDocument, GraphNodeData,
  GraphScope, GraphViewDocument, JobStatus, SemanticIndex,
} from './types'

const LAST_JOB_KEY = 'matlab-atlas:last-job'
const VIEW_KEY_PREFIX = 'matlab-atlas:view:'
type Selection = { type: 'project' } | { type: 'file'; id: string } | { type: 'function'; id: string }
type ViewState = { scope: GraphScope; focusId: string | null; depth: number; limit: number; direction: GraphDirection }
const DEFAULT_VIEW: ViewState = { scope: 'project', focusId: null, depth: 1, limit: 80, direction: 'both' }

/** 使用 Dagre 计算从左到右的节点坐标，并转换成 React Flow 数据结构。 */
function layoutGraph(graph: GraphViewDocument): { nodes: Node[]; edges: Edge[] } {
  const engine = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  engine.setGraph({ rankdir: 'LR', ranksep: 90, nodesep: 34, marginx: 24, marginy: 24 })
  graph.nodes.forEach((item) => engine.setNode(item.id, { width: 188, height: 60 }))
  graph.edges.forEach((edge) => engine.setEdge(edge.source, edge.target))
  dagre.layout(engine)
  return {
    nodes: graph.nodes.map((item) => {
      const pos = engine.node(item.id)
      const className = item.node_type !== 'function' ? `group-node ${item.node_type}-node` : item.is_entry_point ? 'entry-node' : item.is_core ? 'core-node' : item.is_orphan ? 'orphan-node' : ''
      const suffix = item.node_type === 'function' ? '' : ` · ${item.member_count} 函数`
      return {
        id: item.id,
        position: { x: pos.x - 94, y: pos.y - 30 },
        data: { label: `${item.node_type === 'function' ? item.qualified_name : item.label}${suffix}` },
        className,
      }
    }),
    edges: graph.edges.map((edge) => ({
      ...edge,
      type: 'smoothstep',
      animated: false,
      className: edge.is_cycle ? 'cycle-edge' : '',
      markerEnd: { type: MarkerType.ArrowClosed },
    })),
  }
}

/** 从本地存储读取某个任务最后一次使用的图层级和焦点。 */
function restoreViewState(jobId: string): ViewState {
  try {
    const saved = JSON.parse(localStorage.getItem(`${VIEW_KEY_PREFIX}${jobId}`) || 'null') as Partial<ViewState> | null
    if (!saved?.scope) return DEFAULT_VIEW
    return { ...DEFAULT_VIEW, ...saved }
  } catch { return DEFAULT_VIEW }
}

/** 根据数值置信度返回对应的视觉等级名称。 */
function confidenceTone(value: number) {
  if (value < 0.6) return 'low'
  if (value < 0.82) return 'medium'
  return 'high'
}

/** 将字符串数组渲染成紧凑标签；数组为空时展示占位文本。 */
function Tags({ items, empty = '无' }: { items: string[]; empty?: string }) {
  if (!items.length) return <span className="muted">{empty}</span>
  return <div className="tags">{items.map((item) => <span key={item}>{item}</span>)}</div>
}

/** 渲染带标题的列表详情，并自动忽略空列表。 */
function DetailSection({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return <section className="detail-section"><h4>{title}</h4><ul>{items.map((item) => <li key={item}>{item}</li>)}</ul></section>
}

/** 组合整个 MVP 页面，管理任务、数据、选择状态以及三个主面板。 */
export default function App() {
  const [projectPath, setProjectPath] = useState('')
  const [job, setJob] = useState<JobStatus | null>(null)
  const [graph, setGraph] = useState<GraphDocument | null>(null)
  const [graphView, setGraphView] = useState<GraphViewDocument | null>(null)
  const [viewState, setViewState] = useState<ViewState>(DEFAULT_VIEW)
  const [searchQuery, setSearchQuery] = useState('')
  const [semantics, setSemantics] = useState<SemanticIndex | null>(null)
  const [selection, setSelection] = useState<Selection>({ type: 'project' })
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set())
  const [serviceOnline, setServiceOnline] = useState<boolean | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [projects, setProjects] = useState<JobStatus[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)

  const busy = job?.state === 'queued' || job?.state === 'running'
  const laidOut = useMemo(() => graphView ? layoutGraph(graphView) : { nodes: [], edges: [] }, [graphView])
  const byFile = useMemo(() => {
    const grouped = new Map<string, GraphNodeData[]>()
    graph?.nodes.forEach((node) => grouped.set(node.file_path, [...(grouped.get(node.file_path) || []), node]))
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [graph])

  /** 请求一个有限图视图，更新画布并持久化当前导航状态。 */
  const openGraphView = useCallback(async (jobId: string, next: ViewState) => {
    const result = await api.graphView(jobId, next)
    setGraphView(result)
    setViewState(next)
    localStorage.setItem(`${VIEW_KEY_PREFIX}${jobId}`, JSON.stringify(next))
  }, [])

  /** 根据任务就绪标记加载完整索引、当前图视图和三级注释结果。 */
  const loadResults = useCallback(async (current: JobStatus) => {
    if (!current.graph_ready) {
      setGraph(null); setGraphView(null); setSemantics(null); return
    }
    const restoredView = restoreViewState(current.job_id)
    const [fullGraph, visibleGraph, semanticIndex] = await Promise.all([
      api.graph(current.job_id),
      api.graphView(current.job_id, restoredView),
      current.semantics_ready ? api.semantics(current.job_id) : Promise.resolve(null),
    ])
    setGraph(fullGraph); setGraphView(visibleGraph); setViewState(restoredView); setSemantics(semanticIndex)
  }, [])

  /** 从后端读取全部持久化项目，并刷新历史项目列表。 */
  const refreshProjects = useCallback(async () => {
    setProjects(await api.projects())
  }, [])

  useEffect(() => {
    api.health().then(() => {
      setServiceOnline(true)
      return refreshProjects()
    }).catch(() => setServiceOnline(false))
    const lastJob = localStorage.getItem(LAST_JOB_KEY)
    if (lastJob) {
      api.job(lastJob).then(async (restored) => {
        setJob(restored); setProjectPath(restored.project_path); await loadResults(restored)
      }).catch(() => localStorage.removeItem(LAST_JOB_KEY))
    }
  }, [loadResults, refreshProjects])

  useEffect(() => {
    if (!job || !busy) return
    const timer = window.setInterval(async () => {
      try {
        const current = await api.job(job.job_id)
        setJob(current)
        if (current.state === 'completed' || current.state === 'failed') {
          await loadResults(current)
          await refreshProjects()
        }
      } catch (error) { setNotice((error as Error).message) }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [job, busy, loadResults, refreshProjects])

  /** 提交普通项目分析任务，并清空上一个项目的页面状态。 */
  async function startAnalysis() {
    if (!projectPath.trim()) { setNotice('请先输入 MATLAB 项目路径'); return }
    try {
      setNotice(null); setGraph(null); setGraphView(null); setSemantics(null); setSelection({ type: 'project' }); setViewState(DEFAULT_VIEW)
      const created = await api.analyze(projectPath.trim())
      setJob(created); localStorage.setItem(LAST_JOB_KEY, created.job_id)
      await refreshProjects()
    } catch (error) { setNotice((error as Error).message) }
  }

  /** 为当前已完成普通分析的任务启动三级语义注释。 */
  async function startAnnotation() {
    if (!job) return
    try { setNotice(null); setJob(await api.annotate(job.job_id)) }
    catch (error) { setNotice((error as Error).message) }
  }

  /** 切换指定文件在左侧项目树中的展开状态。 */
  function toggleFile(path: string) {
    setExpandedFiles((current) => {
      const next = new Set(current); next.has(path) ? next.delete(path) : next.add(path); return next
    })
  }

  /** 进入目录、文件或函数视图，并同步右侧详情选择。 */
  async function navigateView(scope: GraphScope, focusId: string | null = null, direction: GraphDirection = 'both', depth = 1) {
    if (!job) return
    try {
      setNotice(null)
      await openGraphView(job.job_id, { scope, focusId, direction, depth, limit: scope === 'function' ? 150 : 80 })
      if (scope === 'file' && focusId) setSelection({ type: 'file', id: focusId })
      else if (scope === 'function' && focusId) setSelection({ type: 'function', id: focusId })
      else setSelection({ type: 'project' })
    } catch (error) { setNotice((error as Error).message) }
  }

  /** 搜索完整函数索引并直接打开匹配函数的一层双向邻域。 */
  async function searchGraph() {
    const query = searchQuery.trim().toLocaleLowerCase()
    if (!query || !graph) return
    const match = graph.nodes.find((node) => node.qualified_name.toLocaleLowerCase().includes(query) || node.file_path.toLocaleLowerCase().includes(query))
    if (!match) { setNotice(`没有找到“${searchQuery.trim()}”`); return }
    await navigateView('function', match.id)
  }

  /** 从持久化历史中恢复项目状态、调用图和已有三级注释。 */
  async function openSavedProject(saved: JobStatus) {
    try {
      setNotice(null)
      const restored = await api.job(saved.job_id)
      setJob(restored); setProjectPath(restored.project_path); setSelection({ type: 'project' })
      localStorage.setItem(LAST_JOB_KEY, restored.job_id)
      await loadResults(restored)
      setHistoryOpen(false)
    } catch (error) { setNotice((error as Error).message) }
  }

  /** 删除一个历史项目；若它正在显示，同时清空当前页面。 */
  async function deleteSavedProject(saved: JobStatus) {
    if (!window.confirm(`确认删除 ${saved.project_path} 的本地图和注释吗？`)) return
    try {
      await api.deleteProject(saved.job_id)
      if (job?.job_id === saved.job_id) {
        setJob(null); setGraph(null); setGraphView(null); setSemantics(null); setSelection({ type: 'project' })
        localStorage.removeItem(LAST_JOB_KEY)
        localStorage.removeItem(`${VIEW_KEY_PREFIX}${saved.job_id}`)
      }
      await refreshProjects()
    } catch (error) { setNotice((error as Error).message) }
  }

  const selectedNode = selection.type === 'function' ? graph?.nodes.find((node) => node.id === selection.id) : undefined
  const functionNote: FunctionAnnotation | undefined = selectedNode
    ? semantics?.functions.find((item) => item.symbol_id === selectedNode.id) : undefined
  const filePath = selection.type === 'file' ? selection.id : selectedNode?.file_path
  const fileNote: FileAnnotation | undefined = filePath
    ? semantics?.files.find((item) => item.file_path === filePath) : undefined

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><div className="brand-mark"><Braces size={20} /></div><div><strong>MATLAB Atlas</strong><span>代码结构与语义地图</span></div></div>
        <div className="path-control">
          <FolderTree size={17} />
          <input aria-label="MATLAB 项目路径" placeholder="输入本机 MATLAB 项目目录，例如 D:\projects\signal" value={projectPath} onChange={(event) => setProjectPath(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && !busy && startAnalysis()} />
          <button className="primary" onClick={startAnalysis} disabled={busy}><Play size={16} />生成结构图</button>
          <button className="secondary" onClick={startAnnotation} disabled={!graph || busy}><Sparkles size={16} />生成三级注释</button>
        </div>
        <div className="top-actions"><button className="history-button" onClick={() => setHistoryOpen(true)}><History size={15} />历史项目</button><div className={`service ${serviceOnline ? 'online' : serviceOnline === false ? 'offline' : ''}`}><span />{serviceOnline ? '服务已连接' : serviceOnline === false ? '服务未连接' : '连接中'}</div></div>
      </header>

      <section className="status-strip">
        <div className="status-copy">{busy ? <LoaderCircle className="spin" size={16} /> : job?.state === 'failed' ? <AlertTriangle size={16} /> : <CircleDot size={16} />}<span>{job?.message || '输入项目路径，开始探索代码结构'}</span></div>
        {job && <><span className="job-code">JOB {job.job_id.slice(0, 8)}</span><span>{graphView ? `当前 ${graphView.visible_nodes} / 共 ${graphView.total_nodes}` : `${graph?.nodes.length || 0} 个函数`}</span><span>{graph?.edges.length || 0} 条调用</span></>}
        {notice && <button className="notice" onClick={() => setNotice(null)}>{notice} ×</button>}
      </section>

      <div className="workspace">
        <aside className="tree-panel panel">
          <div className="panel-heading"><div><span className="eyebrow">PROJECT</span><h2>项目结构</h2></div>{graph && <span className="count">{byFile.length}</span>}</div>
          {graph ? <div className="tree">
            <button className={`tree-row project-row ${viewState.scope === 'project' ? 'selected' : ''}`} onClick={() => navigateView('project')}><Network size={16} /><span>项目概览</span></button>
            {byFile.map(([path, nodes]) => <div key={path}>
              <button className={`tree-row ${selection.type === 'file' && selection.id === path ? 'selected' : ''}`} onClick={() => { toggleFile(path); navigateView('file', path) }}>
                {expandedFiles.has(path) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<FileCode2 size={15} /><span title={path}>{path}</span><small>{nodes.length}</small>
              </button>
              {expandedFiles.has(path) && <div className="function-list">{nodes.map((node) => <button key={node.id} className={`tree-row function-row ${selection.type === 'function' && selection.id === node.id ? 'selected' : ''}`} onClick={() => navigateView('function', node.id)}><CircleDot size={12} /><span>{node.label}</span>{node.is_core && <em>核心</em>}</button>)}</div>}
            </div>)}
          </div> : <div className="empty-side"><FolderTree size={28} /><p>尚未载入项目</p><span>结构生成后，文件与函数会显示在这里。</span></div>}
        </aside>

        <section className="graph-panel panel">
          <div className="canvas-heading graph-toolbar">
            <div className="canvas-title"><span className="eyebrow">DEPENDENCY MAP</span><h2>{graphView?.scope === 'project' ? '项目依赖总览' : graphView?.scope === 'directory' ? '目录依赖' : '函数调用图'}</h2></div>
            {graphView && <nav className="breadcrumbs" aria-label="图层级">{graphView.breadcrumbs.map((item, index) => <button key={`${item.scope}:${item.focus_id || 'root'}`} onClick={() => navigateView(item.scope, item.focus_id)}>{index > 0 && <ChevronRight size={11} />}{item.label}</button>)}</nav>}
            <form className="graph-search" onSubmit={(event) => { event.preventDefault(); searchGraph() }}><Search size={14} /><input aria-label="搜索函数或文件" placeholder="搜索函数或文件" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} /><button type="submit">定位</button></form>
            {selection.type === 'function' && <div className="focus-actions">
              <button title="只看调用者" onClick={() => navigateView('function', selection.id, 'callers', viewState.depth)}><ArrowUpFromLine size={13} />调用者</button>
              <button title="只看被调用者" onClick={() => navigateView('function', selection.id, 'callees', viewState.depth)}><ArrowDownToLine size={13} />被调用</button>
              <button title="展开双向邻域" onClick={() => navigateView('function', selection.id, 'both', Math.min(viewState.depth + 1, 5))}><Network size={13} />展开 {Math.min(viewState.depth + 1, 5)} 层</button>
              <button title="恢复所在文件默认视图" onClick={() => selectedNode && navigateView('file', selectedNode.file_path)}><RotateCcw size={13} />恢复文件</button>
            </div>}
          </div>
          {graphView ? <><ReactFlow nodes={laidOut.nodes} edges={laidOut.edges} fitView onlyRenderVisibleElements minZoom={0.15} maxZoom={2}
            onNodeClick={(_, node) => {
              const item = graphView.nodes.find((candidate) => candidate.id === node.id)
              if (item?.node_type === 'file') setSelection({ type: 'file', id: item.file_path })
              else if (item?.node_type === 'function') setSelection({ type: 'function', id: item.id })
              else setSelection({ type: 'project' })
            }}
            onNodeDoubleClick={(_, node) => {
              const item = graphView.nodes.find((candidate) => candidate.id === node.id)
              if (item?.node_type === 'directory') navigateView('directory', item.file_path)
              else if (item?.node_type === 'file') navigateView('file', item.file_path)
              else if (item?.node_type === 'function') navigateView('function', item.id)
            }}>
            <Background color="#ded9cc" gap={24} size={1} /><MiniMap pannable zoomable /><Controls showInteractive={false} />
          </ReactFlow>{graphView.truncated && <div className="graph-limit"><AlertTriangle size={14} />当前仅显示 {graphView.visible_nodes} / {graphView.total_nodes} 个节点，请搜索或聚焦后继续探索。</div>}</> : <div className="hero-empty"><div className="orb"><Network size={46} /></div><span className="eyebrow">READY TO MAP</span><h1>看清代码如何流动</h1><p>生成项目结构图，探索函数之间的调用关系、循环依赖与核心入口。</p><button className="primary large" onClick={startAnalysis} disabled={busy || !projectPath.trim()}><Play size={17} />开始分析</button></div>}
        </section>

        <aside className="detail-panel panel">
          <div className="panel-heading"><div><span className="eyebrow">INSPECTOR</span><h2>{selection.type === 'project' ? '项目语义' : selection.type === 'file' ? '文件详情' : '函数详情'}</h2></div></div>
          <div className="detail-content">
            {selection.type === 'project' && <ProjectDetails semantics={semantics} onAnnotate={startAnnotation} disabled={busy || !graph} />}
            {selection.type === 'file' && <FileDetails path={selection.id} note={fileNote} nodes={graph?.nodes.filter((node) => node.file_path === selection.id) || []} onAnnotate={startAnnotation} disabled={busy} />}
            {selection.type === 'function' && selectedNode && <FunctionDetails node={selectedNode} note={functionNote} conflict={semantics?.conflicts.find((item) => item.symbol_id === selectedNode.id)?.reason} onAnnotate={startAnnotation} disabled={busy} />}
          </div>
        </aside>
      </div>
      {historyOpen && <div className="history-backdrop" onMouseDown={() => setHistoryOpen(false)}>
        <aside className="history-drawer" onMouseDown={(event) => event.stopPropagation()}>
          <div className="history-heading"><div><span className="eyebrow">LOCAL LIBRARY</span><h2>历史项目</h2></div><button aria-label="关闭历史项目" onClick={() => setHistoryOpen(false)}><X size={18} /></button></div>
          <p className="history-intro">调用图和三级注释保存在本机 SQLite 中，关闭页面后仍可恢复。</p>
          <div className="history-list">{projects.length ? projects.map((saved) => <article className="history-card" key={saved.job_id}>
            <button className="history-open" onClick={() => openSavedProject(saved)}><Database size={18} /><div><strong>{saved.project_path.split(/[\\/]/).pop() || saved.project_path}</strong><span>{saved.project_path}</span><small>{new Date(saved.updated_at).toLocaleString()} · {saved.semantics_ready ? '已有三级注释' : saved.graph_ready ? '已有结构图' : saved.message}</small></div></button>
            <button className="history-delete" aria-label={`删除 ${saved.project_path}`} disabled={saved.state === 'queued' || saved.state === 'running'} onClick={() => deleteSavedProject(saved)}><Trash2 size={15} /></button>
          </article>) : <div className="history-empty"><Database size={28} /><p>还没有保存的项目</p><span>完成一次结构分析后会自动出现在这里。</span></div>}</div>
        </aside>
      </div>}
    </main>
  )
}

/** 在语义尚未生成时展示说明和按需生成入口。 */
function AnnotationEmpty({ onAnnotate, disabled }: { onAnnotate: () => void; disabled?: boolean }) {
  return <div className="annotation-empty"><Sparkles size={24} /><h3>三级注释尚未生成</h3><p>普通结构图可以独立使用。需要时再调用模型补充函数、文件和项目语义。</p><button className="secondary" onClick={onAnnotate} disabled={disabled}><Sparkles size={15} />生成三级注释</button></div>
}

/** 将置信度转换成百分比文字和进度条。 */
function Confidence({ value }: { value: number }) {
  return <div className={`confidence ${confidenceTone(value)}`}><span>置信度</span><strong>{Math.round(value * 100)}%</strong><div><i style={{ width: `${value * 100}%` }} /></div></div>
}

/** 展示项目级用途、入口、风险、置信度和冲突统计。 */
function ProjectDetails({ semantics, onAnnotate, disabled }: { semantics: SemanticIndex | null; onAnnotate: () => void; disabled: boolean }) {
  if (!semantics) return <AnnotationEmpty onAnnotate={onAnnotate} disabled={disabled} />
  return <><section className="detail-section"><h4>项目用途</h4><p className="summary">{semantics.project.purpose}</p></section><section className="detail-section"><h4>如何使用</h4><p className="summary">{semantics.project.usage}</p></section><Confidence value={semantics.project.confidence} /><section className="detail-section"><h4>入口函数</h4><Tags items={semantics.project.entry_points} /></section><DetailSection title="项目风险" items={semantics.project.risks} />{semantics.conflicts.length > 0 && <div className="warning"><AlertTriangle size={16} /><span>{semantics.conflicts.length} 项语义需要人工复核</span></div>}</>
}

/** 展示文件包含的函数，以及文件级职责和风险注释。 */
function FileDetails({ path, note, nodes, onAnnotate, disabled }: { path: string; note?: FileAnnotation; nodes: GraphNodeData[]; onAnnotate: () => void; disabled: boolean }) {
  return <><div className="identity"><FileCode2 size={19} /><div><strong>{path.split('/').pop()}</strong><span>{path}</span></div></div><section className="detail-section"><h4>包含函数</h4><Tags items={nodes.map((node) => node.label)} /></section>{note ? <><p className="summary">{note.role}</p><Confidence value={note.confidence} /><DetailSection title="文件风险" items={note.risks} /></> : <AnnotationEmpty onAnnotate={onAnnotate} disabled={disabled} />}</>
}

/** 展示函数元数据、输入输出以及函数级三级语义内容。 */
function FunctionDetails({ node, note, conflict, onAnnotate, disabled }: { node: GraphNodeData; note?: FunctionAnnotation; conflict?: string; onAnnotate: () => void; disabled: boolean }) {
  return <><div className="identity"><Braces size={19} /><div><strong>{node.qualified_name}</strong><span>{node.file_path}:{node.start_line}–{node.end_line}</span></div></div><div className="flags">{node.is_entry_point && <span>入口</span>}{node.is_core && <span>核心</span>}{node.is_orphan && <span>孤立</span>}</div><section className="io-grid"><div><h4>输入</h4><Tags items={node.inputs} /></div><div><h4>输出</h4><Tags items={node.outputs} /></div></section>{conflict && <div className="warning"><AlertTriangle size={16} /><span>{conflict}</span></div>}{note ? <><p className="summary">{note.summary}</p><Confidence value={note.confidence} /><DetailSection title="数据流" items={note.data_flow} /><DetailSection title="副作用" items={note.side_effects} /><DetailSection title="潜在风险" items={note.risks} /><DetailSection title="待确认问题" items={note.open_questions} /></> : <AnnotationEmpty onAnnotate={onAnnotate} disabled={disabled} />}</>
}
