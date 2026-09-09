import { useState } from 'react'
import AgentLab from '../components/AgentLab'
import LearningLab from '../components/LearningLab'
import LazyModule from '../components/LazyModule'
import { downloadText } from '../store'

/** Automation / API / iframe / scroll / new-tab test surface */
export default function LabPage() {
  const [scrollLog, setScrollLog] = useState([])

  return (
    <div className="sz-wrap" data-testid="lab-page">
      <h1 style={{ fontSize: 40, fontWeight: 600 }}>Test Lab</h1>
      <p style={{ color: 'var(--muted)' }}>
        Built for automation testing, API testing, and agentic / RAG / chatbot testing across frameworks.
      </p>

      <LearningLab />

      <section style={{ marginTop: 28 }} data-testid="lab-agentic">
        <AgentLab />
      </section>

      <section style={{ marginTop: 28 }} data-testid="lab-newtab">
        <h2>New tab navigation</h2>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <a className="sz-btn sz-btn-blue" href="/shop/store" target="_blank" rel="noreferrer" data-testid="lab-open-store-tab">Open Store (new tab)</a>
          <a className="sz-btn sz-btn-ghost" href="/api/products?limit=3" target="_blank" rel="noreferrer" data-testid="lab-open-api-tab">Open API (new tab)</a>
          <a className="sz-btn sz-btn-dark" href="/docs" target="_blank" rel="noreferrer" data-testid="lab-open-docs-tab">Open API docs</a>
        </div>
      </section>

      <section style={{ marginTop: 28 }} data-testid="lab-download">
        <h2>Download options</h2>
        <button type="button" className="sz-btn sz-btn-blue" data-testid="lab-download-sample"
          onClick={() => downloadText('shopzone-sample.csv', 'id,title,price\nPR-L001,ThinkTrail,78900\n', 'text/csv')}>
          Download sample CSV
        </button>
        <button type="button" className="sz-btn sz-btn-ghost" style={{ marginLeft: 8 }} data-testid="lab-download-json"
          onClick={() => downloadText('agent-fixture.json', JSON.stringify({ mode: 'multi_agent', framework: 'langgraph' }, null, 2), 'application/json')}>
          Download agent fixture
        </button>
      </section>

      <section style={{ marginTop: 28 }} data-testid="lab-iframe">
        <h2>IFrame embed</h2>
        <iframe
          className="sz-lab-iframe"
          title="embedded-store"
          data-testid="lab-iframe-store"
          src="/shop/store"
        />
        <iframe
          className="sz-lab-iframe"
          style={{ marginTop: 12, minHeight: 280 }}
          title="embedded-api"
          data-testid="lab-iframe-api"
          src="/api/products?limit=2"
        />
      </section>

      <section style={{ marginTop: 28 }} data-testid="lab-scroll">
        <h2>Scroll + lazy load modules</h2>
        <p className="sz-meta">Scroll down — modules load via IntersectionObserver ({scrollLog.join(', ') || 'none yet'})</p>
        <div style={{ height: 400 }} />
        <LazyModule testId="lab-lazy-1" className="sz-module">
          <h2 onLoad={() => {}}>Module A loaded</h2>
          <p className="lead" data-testid="lab-lazy-1-content">First lazy module visible</p>
        </LazyModule>
        <div style={{ height: 300 }} />
        <LazyModule testId="lab-lazy-2" className="sz-module dark">
          <h2>Module B loaded</h2>
          <p className="lead" data-testid="lab-lazy-2-content">Second lazy module for scroll tests</p>
        </LazyModule>
        <div style={{ height: 300 }} />
        <LazyModule testId="lab-lazy-3" className="sz-module gray">
          <h2>Module C loaded</h2>
          <button type="button" className="sz-btn sz-btn-blue" data-testid="lab-scroll-mark" onClick={() => setScrollLog((s) => [...s, 'clicked'])}>
            Mark interaction
          </button>
        </LazyModule>
      </section>

      <section style={{ marginTop: 28 }} data-testid="lab-forms">
        <h2>Form controls</h2>
        <input className="sz-input" data-testid="lab-text" placeholder="Text input" />
        <select className="sz-input" data-testid="lab-select" style={{ marginTop: 8 }}>
          <option>Option A</option>
          <option>Option B</option>
        </select>
        <textarea className="sz-input" data-testid="lab-textarea" style={{ marginTop: 8 }} rows={3} placeholder="Textarea" />
      </section>
    </div>
  )
}
