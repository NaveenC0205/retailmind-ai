import { useEffect, useState } from 'react'
import {
  chatStream,
  fetchAgentStatus,
  fetchDatasets,
  fetchTools,
  isAdmin,
  isLoggedIn,
  runEval,
} from '../api'
import ChatWidget from './ChatWidget'

const MODES = [
  { id: 'multi_agent', label: 'LangGraph multi-agent' },
  { id: 'agent', label: 'Single agent' },
  { id: 'rag', label: 'RAG only' },
  { id: 'chat', label: 'Chat (no tools)' },
  { id: 'auto', label: 'Auto route' },
]

const PERSONAS = ['customer', 'product', 'owner']

const PROBES = [
  { mode: 'multi_agent', persona: 'customer', text: 'Search for iPhone and give me the prices' },
  { mode: 'multi_agent', persona: 'customer', text: 'Buy iPhone 15 with UPI' },
  { mode: 'rag', persona: 'customer', text: 'What is the return window for electronics?' },
  { mode: 'agent', persona: 'customer', text: 'Where is my order OR-20001?' },
  { mode: 'chat', persona: 'customer', text: 'Hi, what can you help me with today?' },
  { mode: 'multi_agent', persona: 'product', text: 'Compare this with similar products' },
  { mode: 'multi_agent', persona: 'owner', text: 'List pending orders' },
  { mode: 'agent', persona: 'owner', text: 'Which products need restock?' },
  { mode: 'multi_agent', persona: 'owner', text: 'Approve the oldest pending order' },
]

export default function AgentLab() {
  const [persona, setPersona] = useState(isAdmin() ? 'owner' : 'customer')
  const [mode, setMode] = useState('multi_agent')
  const [promptVersion, setPromptVersion] = useState('v1')
  const [message, setMessage] = useState(PROBES[0].text)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [events, setEvents] = useState([])
  const [health, setHealth] = useState(null)
  const [tools, setTools] = useState(null)
  const [datasets, setDatasets] = useState([])
  const [evalOut, setEvalOut] = useState(null)
  const [evalBusy, setEvalBusy] = useState(false)
  const [dataset, setDataset] = useState('agents/task_trajectories')

  useEffect(() => {
    fetchAgentStatus().then(setHealth)
    fetchTools().then(setTools).catch(() => setTools(null))
    fetchDatasets().then((d) => setDatasets(d.datasets || [])).catch(() => setDatasets([]))
  }, [])

  async function runProbe(text = message, nextMode = mode, nextPersona = persona) {
    const raw = (text || '').trim()
    if (!raw) return
    setBusy(true)
    setEvents([])
    setResult(null)
    try {
      const final = await chatStream(raw, null, (ev) => {
        if (ev.type === 'agent' || ev.type === 'start' || ev.type === 'final') {
          setEvents((xs) => [...xs.slice(-24), { type: ev.type, data: ev.data }])
        }
      }, { persona: nextPersona, mode: nextMode, promptVersion, productId: nextPersona === 'product' ? 'PR-P003' : undefined })
      setResult(final)
    } catch (e) {
      setResult({ answer: String(e.message || e), error: true })
    } finally {
      setBusy(false)
    }
  }

  async function kickEval() {
    setEvalBusy(true)
    setEvalOut(null)
    try {
      const out = await runEval({
        dataset,
        suite: dataset.includes('task_trajectories') ? 'agents' : undefined,
        prompt_version: promptVersion,
        provider: 'mock',
        concurrency: 2,
      })
      setEvalOut(out)
    } catch (e) {
      setEvalOut({ error: String(e.message || e) })
    } finally {
      setEvalBusy(false)
    }
  }

  const ownerLocked = persona === 'owner' && !isAdmin()

  return (
    <div data-testid="agent-lab">
      <div className="sz-lab-card" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>Framework tester</h2>
        <p className="sz-meta">
          Switch LangGraph, single-agent, RAG, or chat. Owner persona needs seller login
          ({isLoggedIn() ? (isAdmin() ? 'owner session' : 'customer session') : 'guest'}).
          Health: {health ? `${health.llm_backend} · ${health.llm_live ? 'live' : 'mock'} · ${health.tools} tools · teams ${(health.teams || []).join('/')}` : '…'}
        </p>
        <div className="sz-lab-grid">
          <label className="sz-meta">Persona
            <select className="sz-input" data-testid="lab-persona" value={persona} onChange={(e) => setPersona(e.target.value)}>
              {PERSONAS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="sz-meta">Mode / framework
            <select className="sz-input" data-testid="lab-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
              {MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
          <label className="sz-meta">Prompt
            <select className="sz-input" data-testid="lab-prompt" value={promptVersion} onChange={(e) => setPromptVersion(e.target.value)}>
              <option value="v1">v1</option>
              <option value="v2">v2</option>
            </select>
          </label>
        </div>
        {ownerLocked && <p className="sz-meta">Sign in as a shop owner to run the seller team.</p>}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '12px 0' }}>
          {PROBES.map((p) => (
            <button
              key={p.text}
              type="button"
              className="sz-btn sz-btn-ghost"
              style={{ fontSize: 11, padding: '6px 10px' }}
              data-testid="lab-probe"
              onClick={() => {
                setPersona(p.persona)
                setMode(p.mode)
                setMessage(p.text)
                runProbe(p.text, p.mode, p.persona)
              }}
            >
              {p.persona}/{p.mode.split('_')[0]}: {p.text.slice(0, 28)}
            </button>
          ))}
        </div>
        <textarea className="sz-input" data-testid="lab-agent-input" rows={3} value={message} onChange={(e) => setMessage(e.target.value)} />
        <button type="button" className="sz-btn sz-btn-blue" style={{ marginTop: 8 }} data-testid="lab-agent-run" disabled={busy || ownerLocked} onClick={() => runProbe()}>
          {busy ? 'Running team…' : 'Run agent'}
        </button>
        {!!events.length && (
          <p className="sz-meta" style={{ marginTop: 10 }} data-testid="lab-agent-events">
            Stream: {events.map((e) => e.data?.type || e.type).join(' → ')}
          </p>
        )}
        {result && (
          <pre className="sz-lab-out" data-testid="lab-agent-out">{
            JSON.stringify({
              answer: result.answer,
              mode: result.mode,
              framework: result.framework,
              persona: result.persona || persona,
              terminal_state: result.terminal_state,
              entry_agent: result.entry_agent,
              trajectory: result.trajectory,
              sub_results: (result.sub_results || []).map((s) => s.agent),
              suggestions: result.suggestions,
              trace_id: result.trace_id,
              run_id: result.run_id,
              approval_id: result.approval_id,
              citations: result.citations,
              error: result.error,
            }, null, 2)
          }</pre>
        )}
      </div>

      <div className="sz-lab-card" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>Eval runner</h2>
        <p className="sz-meta">Hits POST /api/eval/run against the mock provider so framework gates stay deterministic.</p>
        <select className="sz-input" data-testid="lab-dataset" value={dataset} onChange={(e) => setDataset(e.target.value)}>
          {(datasets.length ? datasets : [{ name: 'agents/task_trajectories' }]).map((d) => (
            <option key={d.name || d.error} value={d.name}>{d.name}{d.items ? ` (${d.items})` : ''}</option>
          ))}
        </select>
        <button type="button" className="sz-btn sz-btn-dark" style={{ marginTop: 8 }} data-testid="lab-eval-run" disabled={evalBusy} onClick={kickEval}>
          {evalBusy ? 'Evaluating…' : 'Run eval (mock)'}
        </button>
        {evalOut && (
          <pre className="sz-lab-out" data-testid="lab-eval-out">{JSON.stringify(evalOut.summary || evalOut, null, 2)}</pre>
        )}
      </div>

      <div className="sz-lab-card">
        <h2 style={{ marginTop: 0 }}>Introspection</h2>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <a className="sz-btn sz-btn-ghost" href="/health" target="_blank" rel="noreferrer">/health</a>
          <a className="sz-btn sz-btn-ghost" href="/api/tools" target="_blank" rel="noreferrer">/api/tools ({tools?.count || '…'})</a>
          <a className="sz-btn sz-btn-ghost" href="/api/runs?limit=10" target="_blank" rel="noreferrer">/api/runs</a>
          <a className="sz-btn sz-btn-ghost" href="/docs" target="_blank" rel="noreferrer">OpenAPI</a>
        </div>
        {result?.trace_id && (
          <p className="sz-meta" style={{ marginTop: 10 }}>
            Last trace: <a href={`/api/traces/${result.trace_id}`} target="_blank" rel="noreferrer">{result.trace_id}</a>
            {result.run_id ? <> · run <a href={`/api/runs/${result.run_id}`} target="_blank" rel="noreferrer">{result.run_id}</a></> : null}
          </p>
        )}
      </div>

      {!ownerLocked && (
        <p className="sz-meta" style={{ marginTop: 16 }}>
          Live chat below uses the persona, mode, and prompt you selected — same APIs as shop and seller dashboard.
        </p>
      )}
      {!ownerLocked && (
        <ChatWidget
          key={`${persona}-${mode}-${promptVersion}`}
          persona={persona}
          mode={mode}
          promptVersion={promptVersion}
          productContext={persona === 'product' ? { id: 'PR-P003' } : null}
        />
      )}
    </div>
  )
}
