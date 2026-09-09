import { useEffect, useRef, useState } from 'react'
import { chatStream, fetchAgentStatus, fetchJson } from '../api'
import { downloadText } from '../store'
import { LESSONS, runExercise, summarize, toolNames } from '../learning/scenarios'

export default function LearningLab() {
  const [lessonId, setLessonId] = useState(LESSONS[0].id)
  const lesson = LESSONS.find((l) => l.id === lessonId)
  const [compare, setCompare] = useState(false)
  const [repeats, setRepeats] = useState(1)
  const [promptVersion, setPromptVersion] = useState('v1')
  const [records, setRecords] = useState([])
  const [busy, setBusy] = useState(false)
  const [health, setHealth] = useState(null)
  const [sources, setSources] = useState(null)
  const [sourceError, setSourceError] = useState('')
  const [review, setReview] = useState({})
  const [notes, setNotes] = useState('')
  const [runInfo, setRunInfo] = useState(null)
  const [stopped, setStopped] = useState(false)
  const controller = useRef(null)
  useEffect(() => {
    fetchAgentStatus().then(setHealth)
    fetchJson('/api/learning/policies').then((data) => setSources(data.sources)).catch((e) => setSourceError(e.message))
    return () => controller.current?.abort()
  }, [])
  useEffect(() => {
    const stop = () => controller.current?.abort()
    window.addEventListener('shopzone-store', stop)
    window.addEventListener('storage', stop)
    return () => { window.removeEventListener('shopzone-store', stop); window.removeEventListener('storage', stop) }
  }, [])
  async function run() {
    if (controller.current) return
    const ctrl = new AbortController()
    controller.current = ctrl
    setBusy(true); setStopped(false); setRecords([]); setReview({}); setNotes('')
    const modes = compare ? ['chat', 'rag', 'agent', 'multi_agent'] : [lesson.mode]
    setRunInfo({ lesson: lesson.title, lessonId: lesson.id, modes, repeats, promptVersion, startedAt: new Date().toISOString() })
    try {
      await runExercise({ lesson, modes, repeats, promptVersion, signal: ctrl.signal, send: chatStream, onRecord: (record) => setRecords((rs) => [...rs, record]) })
    } finally {
      setStopped(ctrl.signal.aborted); setBusy(false); controller.current = null
    }
  }
  function selectLesson(id) { setLessonId(id); setRecords([]); setReview({}); setNotes(''); setRunInfo(null); setStopped(false) }
  const summary = summarize(records)
  return <section className="sz-learning" data-testid="learning-lab" aria-labelledby="learning-title">
    <div className="sz-learning-heading"><div><span className="sz-learning-eyebrow">LEARN BY TESTING</span><h2 id="learning-title">AI testing workshop</h2><p>Run an exercise, inspect the evidence, and write your verdict.</p></div><span className="sz-learning-provider">{health?.unavailable ? 'Provider status unavailable' : health ? `${health.llm_live ? 'Live provider' : 'Mock provider'} · ${health.llm_backend}` : 'Checking provider…'}</span></div>
    <div className="sz-learning-layout">
      <nav aria-label="Testing lessons" className="sz-learning-lessons">{LESSONS.map((l) => <button type="button" key={l.id} disabled={busy} aria-pressed={lessonId === l.id} onClick={() => selectLesson(l.id)}>{l.title}</button>)}</nav>
      <div className="sz-learning-workspace">
        <h3>{lesson.title}</h3><p>{lesson.goal}</p>
        <ol className="sz-learning-turns">{lesson.turns.map((text) => <li key={text}>{text}</li>)}</ol>
        <div className="sz-learning-controls">
          <label>Runs per mode<select className="sz-input" disabled={busy} value={repeats} onChange={(e) => setRepeats(Number(e.target.value))}>{[1, 3, 5].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          <label>Prompt version<select className="sz-input" disabled={busy} value={promptVersion} onChange={(e) => setPromptVersion(e.target.value)}><option>v1</option><option>v2</option></select></label>
          <label className="sz-learning-checkbox"><input type="checkbox" checked={compare} disabled={busy} onChange={(e) => setCompare(e.target.checked)} />Compare all four modes</label>
        </div>
        <p className="sz-meta">{lesson.turns.length * repeats * (compare ? 4 : 1)} requests, run sequentially. Each repeat starts a fresh conversation. Results stay on this page; export before leaving. Uses this server’s configured provider; live requests may incur provider charges.</p>
        <div className="sz-learning-actions"><button type="button" className="sz-btn sz-btn-blue" disabled={busy} onClick={run}>{busy ? 'Running exercise…' : 'Run exercise'}</button>{busy && <button type="button" className="sz-btn sz-btn-ghost" onClick={() => controller.current?.abort()}>Stop</button>}<button type="button" className="sz-btn sz-btn-ghost" disabled={!records.length || busy} onClick={() => downloadText('ai-testing-report.json', JSON.stringify({ ...runInfo, status: stopped ? 'stopped' : 'complete', summary, records, manualReview: lesson.review.map((question, i) => ({ question, verdict: review[i] || 'Not reviewed' })), notes }, null, 2), 'application/json')}>Export report</button></div>
        <p role="status" aria-live="polite">{busy ? `${records.length} replies collected…` : stopped ? 'Stopped. Partial results are available below.' : records.length ? 'Exercise finished. Review the evidence below.' : 'Choose a lesson to begin. No account is required for these exercises.'}</p>
      </div>
    </div>
    {!!records.length && <div className="sz-learning-results">
      <div className="sz-learning-stats"><span><strong>{summary.requests}</strong> requests completed</span><span><strong>{summary.checksPassed}/{summary.checksTotal}</strong> structural checks passed</span><span><strong>{summary.errors}</strong> request errors</span><span><strong>{summary.p95Ms ?? '—'}</strong> p95 response time (ms)</span></div>
      <p>These checks verify response structure and reported behavior. They do not establish factual accuracy, complete security, or production readiness. Response time includes the network; small samples are only illustrative.</p>
      {records.map((r, index) => <details className="sz-learning-record" key={index} open={records.length === 1}><summary>{r.mode} · run {r.repeat} · turn {r.turn} · {r.error ? 'Request failed' : r.checks.every((c) => c.passed) ? 'Structural checks passed' : 'Review failed checks'} · {r.elapsedMs} ms</summary><p><strong>You asked:</strong> {r.message}</p>{r.error ? <p role="alert">{r.error}</p> : <><p className="sz-learning-answer">{r.result.answer}</p><ul>{r.checks.map((c) => <li key={c.id}>{c.passed ? 'Pass' : 'Fail'}: {c.label}</li>)}</ul><p><strong>Actual mode:</strong> {r.result.mode || 'Not reported'} · <strong>Provider:</strong> {r.result.llm_backend || 'Not reported'} · <strong>Tools:</strong> {toolNames(r.result).join(', ') || 'None reported'}</p><p><strong>Citations:</strong> {(r.result.citations || []).join(', ') || 'None reported'}</p><details><summary>Inspect response evidence</summary><pre className="sz-lab-out">{JSON.stringify(r.result, null, 2)}</pre></details></>}</details>)}
      <h3>Your quality review</h3><p>Compare the actual answer with the source documents and tool results. Record your observations; these verdicts are your assessment.</p>{lesson.review.map((question, i) => <label className="sz-learning-review" key={question}>{question}<select className="sz-input" value={review[i] || ''} onChange={(e) => setReview((r) => ({ ...r, [i]: e.target.value }))}><option value="">Not reviewed</option><option>Pass</option><option>Fail</option><option>Needs investigation</option></select></label>)}
      <label>Evidence and bug notes<textarea className="sz-input" rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Expected behavior, actual behavior, evidence, and steps to reproduce" /></label>
    </div>}
    <details className="sz-learning-sources"><summary>Policy source library · verify answers and citations</summary><p>Match the returned citation ID to a source below. Check the effective dates before using a policy as your reference answer.</p>{sourceError ? <p role="alert">Could not load sources: {sourceError}</p> : sources === null ? <p>Loading policies…</p> : sources.length === 0 ? <p>No public policy sources have been loaded yet.</p> : sources.map((source) => <details key={source.chunk_id}><summary>{source.chunk_id} · {source.heading}</summary><p>Version {source.version} · Effective from {source.effective_from || 'Not provided'} · Effective until {source.effective_to || 'No end date'}</p><pre className="sz-lab-out">{source.content}</pre></details>)}</details>
    <div className="sz-learning-next"><h3>Try next</h3><p>{lesson.next}</p><p>For advanced practice, use the Framework tester below for custom prompts, seller approvals, and authenticated order flows. Use a test account for actions that change orders.</p><details><summary>What to learn after these exercises</summary><ul><li>RAG: recall@k measures how much relevant evidence was retrieved; precision@k measures how much retrieved evidence is relevant. Both require a labeled reference dataset.</li><li>Agents: inspect tool arguments, execution order, authorization failures, approval steps, and duplicate side effects.</li><li>Reliability: compare repeated results, simulate failed tools, and measure concurrent load in an isolated test environment. This workshop runs sequentially.</li><li>Evaluation: compare prompt versions on the same cases. Keep a held-out dataset to avoid tuning only for examples you have already seen.</li></ul></details></div>
  </section>
}
