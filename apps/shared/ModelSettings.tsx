/** 两个应用共用的模型设置面板：密钥只写不读，保存时只提交修改的白名单字段。 */
import { useEffect, useRef, useState } from 'react'
import './model-settings.css'

type SettingsView = {
  values: Record<string, string | number | null>
  api_key_configured: boolean
  api_key_source: 'environment' | 'file' | 'missing'
  api_key_env: string
  overridden_fields: string[]
  env_file: string
  revision: string
}

type NumericField = { name: string; label: string; min: number; max?: number; step?: number | 'any' }
const GROUPS: { title: string; fields: NumericField[] }[] = [
  { title: '并发控制', fields: [
    { name: 'max_workers', label: '静态分析 Worker 并发', min: 1, max: 64 },
    { name: 'max_agents', label: '模型 API 总并发上限', min: 1, max: 64 },
    { name: 'semantic_max_agents', label: '语义文件聚合并发', min: 1, max: 16 },
    { name: 'migration_max_agents', label: '迁移 WCC 并发', min: 1, max: 16 },
    { name: 'migration_chunk_max_agents', label: '迁移 ActChunk 并发', min: 1, max: 16 },
  ] },
  { title: '请求与重试', fields: [
    { name: 'timeout_seconds', label: '请求超时（秒）', min: 0, max: 600, step: 'any' },
    { name: 'hard_timeout_seconds', label: '整次模型硬超时（秒）', min: 1, max: 1800, step: 'any' },
    { name: 'temperature', label: '温度 Temperature', min: 0, max: 2, step: 'any' },
    { name: 'sdk_max_retries', label: '网络请求重试次数', min: 0, max: 10 },
    { name: 'response_retries', label: '结构化响应重试次数', min: 0, max: 5 },
  ] },
  { title: '上下文与输出预算', fields: [
    { name: 'model_context_window_tokens', label: '模型上下文窗口（tokens）', min: 4096, max: 10000000 },
    { name: 'context_safety_margin_tokens', label: '上下文安全余量（tokens）', min: 256, max: 1000000 },
    { name: 'semantic_max_output_tokens', label: '语义最大输出（tokens）', min: 256, max: 384000 },
    { name: 'migration_max_output_tokens', label: '迁移 Act 最大输出（tokens）', min: 256, max: 384000 },
    { name: 'migration_reason_max_output_tokens', label: '迁移 Reason 最大输出（tokens）', min: 256, max: 65536 },
  ] },
  { title: '语义流水线', fields: [
    { name: 'semantic_token_budget', label: '语义单元目标输入（tokens）', min: 512 },
    { name: 'semantic_max_functions_per_unit', label: '每单元目标函数数', min: 1, max: 256 },
    { name: 'semantic_confidence_threshold', label: '语义置信度阈值', min: 0, max: 1, step: 'any' },
    { name: 'semantic_max_attempts', label: '语义单元最大尝试次数', min: 1, max: 10 },
  ] },
]

async function settingsRequest(init?: RequestInit): Promise<SettingsView> {
  const response = await fetch('/api/settings/llm', {
    ...init, cache: 'no-store', headers: { 'Content-Type': 'application/json' },
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '无法保存模型设置')
  return data
}

export function ModelSettings({ onClose }: { onClose: () => void }) {
  const [saved, setSaved] = useState<SettingsView | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [apiKey, setApiKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const dialog = useRef<HTMLElement>(null)
  const form = useRef<HTMLFormElement>(null)

  function accept(view: SettingsView) {
    setSaved(view)
    setValues(Object.fromEntries(Object.entries(view.values).map(([name, value]) => [name, String(value ?? '')])))
    setApiKey(''); setClearKey(false)
  }

  useEffect(() => {
    let cancelled = false
    const previousFocus = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    settingsRequest().then((view) => { if (!cancelled) accept(view) })
      .catch((failure) => { if (!cancelled) setError((failure as Error).message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    dialog.current?.focus()
    return () => {
      cancelled = true
      document.body.style.overflow = previousOverflow
      previousFocus?.focus()
    }
  }, [])

  const disabled = (name: string) => busy || !!saved?.overridden_fields.includes(name)
  const keyDisabled = busy || saved?.api_key_source === 'environment'
  const changed = saved ? Object.keys(values).filter((name) => values[name] !== String(saved.values[name] ?? '')) : []
  const dirty = changed.length > 0 || !!apiKey.trim() || clearKey
  const update = (name: string, value: string) => setValues((previous) => ({ ...previous, [name]: value }))
  const lockedLabel = (name: string) => saved?.overridden_fields.includes(name) ? ' · 由进程环境控制' : ''

  async function reload() {
    try { setLoading(true); setError(''); setMessage(''); accept(await settingsRequest()) }
    catch (failure) { setError((failure as Error).message) }
    finally { setLoading(false) }
  }

  async function save() {
    if (!saved || !form.current) return
    if (!form.current.checkValidity()) {
      // 展开高级字段后再定位错误，避免隐藏输入框阻止整个表单保存。
      const advanced = form.current.querySelector('details')
      if (advanced) advanced.open = true
      form.current.reportValidity()
      return
    }
    const numbers = new Set(GROUPS.flatMap((group) => group.fields.map((field) => field.name)))
    const patch = Object.fromEntries(changed.map((name) => [name,
      numbers.has(name) ? Number(values[name]) : name === 'thinking_mode' ? values[name] || null : values[name].trim(),
    ]))
    try {
      setBusy(true); setError(''); setMessage('')
      accept(await settingsRequest({ method: 'PUT', body: JSON.stringify({
        values: patch, api_key: apiKey.trim() || null, clear_api_key: clearKey, revision: saved.revision,
      }) }))
      setMessage('已保存。新任务和续跑开始时使用新配置；正在执行的任务不变。')
    } catch (failure) { setError((failure as Error).message) }
    finally { setBusy(false); dialog.current?.scrollTo({ top: 0 }) }
  }

  return <div className="model-settings-backdrop" onMouseDown={(event) => {
    if (event.target === event.currentTarget && !busy) onClose()
  }}>
    <aside className="model-settings" role="dialog" aria-modal="true" aria-labelledby="model-settings-title"
      tabIndex={-1} ref={dialog} onKeyDown={(event) => {
        if (event.key === 'Escape' && !busy) onClose()
        if (event.key !== 'Tab') return
        const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), summary') || [])
          .filter((element) => element.getClientRects().length > 0)
        const first = controls[0]; const last = controls[controls.length - 1]
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {
          event.preventDefault(); last?.focus()
        } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }}>
      <header><div><span>MODEL CONFIGURATION</span><h2 id="model-settings-title">模型与 .env 设置</h2></div>
        <button type="button" aria-label="关闭模型设置" disabled={busy} onClick={onClose}>×</button></header>
      <p>模型名称、URL、密钥和运行参数保存到本地 .env。两个应用使用同一份配置文件时会共享设置。</p>
      {error && <div className="model-settings-error" role="alert">{error}</div>}
      {message && <div className="model-settings-success" role="status">{message}</div>}
      {loading ? <p>正在读取配置…</p> : saved && <form ref={form} noValidate onSubmit={(event) => { event.preventDefault(); void save() }}>
        <label><span>模型名称{lockedLabel('model')}</span><input aria-label="模型名称" required maxLength={200}
          value={values.model} disabled={disabled('model')} onChange={(event) => update('model', event.target.value)} /></label>
        <label><span>API Base URL{lockedLabel('base_url')}</span><input aria-label="API Base URL" type="url" required
          placeholder="https://your-provider.example/v1" value={values.base_url} disabled={disabled('base_url')}
          onChange={(event) => update('base_url', event.target.value)} /></label>
        <p className="model-settings-hint">填写接口根地址，不是 /chat/completions。修改 URL 会改变后续源码和密钥的发送目标，请确认地址可信；保存不会联网测试。</p>
        {values.base_url?.startsWith('http:') && <p className="model-settings-warning">HTTP 不加密请求和密钥，仅应在可信本地环境使用。</p>}
        <label><span>API Key · {saved.api_key_source === 'environment' ? '由进程环境控制' : saved.api_key_configured ? '已配置' : '未配置'}</span>
          <input aria-label="API Key" type="password" autoComplete="new-password" spellCheck={false} maxLength={8192}
            placeholder={saved.api_key_configured ? '留空保留现有密钥；输入新值替换' : '输入新密钥'} value={apiKey}
            disabled={keyDisabled || clearKey} onChange={(event) => setApiKey(event.target.value)} /></label>
        <label className="model-settings-checkbox"><input type="checkbox" checked={clearKey} disabled={keyDisabled}
          onChange={(event) => { setClearKey(event.target.checked); setApiKey('') }} /><span>清除已保存的 API Key（保存后生效）</span></label>
        <p className="model-settings-hint">写入 {saved.api_key_env}。已有密钥不回显、不存入浏览器存储；留空不会删除。清除只移除本地配置，不撤销提供方的密钥。</p>
        {saved.api_key_source === 'environment' && <p className="model-settings-warning">启动环境中的密钥优先于 .env，不能在此覆盖或清除。请修改启动环境后重启服务。</p>}
        <details className="model-settings-advanced"><summary>高级参数 · 请求、预算与语义流水线</summary>
          <label><span>思考模式{lockedLabel('thinking_mode')}</span><select aria-label="思考模式" value={values.thinking_mode}
            disabled={disabled('thinking_mode')} onChange={(event) => update('thinking_mode', event.target.value)}>
            <option value="enabled">启用 enabled</option><option value="disabled">禁用 disabled</option><option value="">不发送此参数</option>
          </select></label>
          {GROUPS.map((group) => <fieldset key={group.title}><legend>{group.title}</legend><div className="model-settings-grid">
            {group.fields.map((field) => <label key={field.name}><span>{field.label}{lockedLabel(field.name)}</span>
              <input type="number" required aria-label={field.label} min={field.min} max={field.max} step={field.step || 1}
                value={values[field.name]} disabled={disabled(field.name)} onChange={(event) => update(field.name, event.target.value)} /></label>)}
          </div></fieldset>)}
          <p className="model-settings-hint">模型输入预算 + 最大输出 + 安全余量必须容纳于真实上下文窗口。不同提供方支持的思考模式和输出上限可能不同。</p>
        </details>
        <div className="model-settings-meta">配置文件<code>{saved.env_file}</code></div>
        {!!saved.overridden_fields.length && <p className="model-settings-warning">标为“由进程环境控制”的字段只读。需要在启动环境中修改，避免出现保存后却未生效的情况。</p>}
        <footer><button type="button" onClick={reload} disabled={busy}>重新加载</button>
          <button className="model-settings-save" type="submit" disabled={busy || !dirty}>{busy ? '保存中…' : '保存配置'}</button></footer>
        <p className="model-settings-hint">重新加载或关闭面板会放弃未保存的修改。.env 是本地明文文件，请勿提交或公开；此应用不应暴露到公网。</p>
      </form>}
      {!loading && !saved && <button type="button" onClick={reload}>重新读取配置</button>}
    </aside>
  </div>
}
