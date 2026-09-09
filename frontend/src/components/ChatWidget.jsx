import { useEffect, useMemo, useRef, useState } from 'react'
import { chatActorId, chatStream, fetchAgentStatus, getToken, isAdmin, isLoggedIn } from '../api'

function customerCopy(loggedIn, name) {
  if (loggedIn) {
    return {
      title: 'ShopZone Assistant',
      sub: 'Your personal shopper · this session only',
      welcome: `Hi${name ? ` ${name}` : ''}! This is your private chat for this visit. I can show your orders, check payment status, and place items you name after you pick UPI, Card, or Cash on delivery.`,
      suggests: [
        'Show my recent orders',
        'What payment was used on my latest order?',
        'Buy this with UPI',
        'What payment methods can I use?',
      ],
    }
  }
  return {
    title: 'ShopZone Assistant',
    sub: 'Guest session · sign in to order',
    welcome: 'Hi! This guest chat is only for this browser session. I can search the catalogue and prices without an account. Sign in for a private order assistant.',
    suggests: [
      'Search for iPhone and give me the prices',
      'Laptops under 80000 with 16GB RAM',
      'What payment methods can I use?',
      'Sign in to place an order',
    ],
  }
}

const PERSONAS = {
  product: {
    title: 'Product Specialist',
    sub: 'This product · this session only',
    welcome: 'Ask about this product’s specs, stock, warranty, or say “buy this with UPI” after you sign in. This thread is only for this visit.',
    suggests: [
      'Compare with similar products',
      'Check inventory for this item',
      'What payment methods can I use?',
      'Buy this with UPI',
    ],
  },
  owner: {
    title: 'Seller Ops Concierge',
    sub: 'Owner agents · this session only',
    welcome: 'Owner multi-agent desk for this visit: pending orders, approve/reject, low stock, and ops policy.',
    suggests: [
      'List pending orders',
      'Approve the oldest pending order',
      'Which products need restock?',
      'Summarize today’s order queue',
    ],
  },
}

function welcomeFor(persona, loggedIn, name) {
  if (persona === 'customer') return customerCopy(loggedIn, name)
  return PERSONAS[persona] || customerCopy(loggedIn, name)
}

const TEAM_AGENTS = {
  customer: ['shopping', 'product', 'order', 'checkout', 'policy', 'refund', 'support', 'recommendation'],
  product: ['product', 'shopping', 'policy', 'recommendation', 'checkout'],
  owner: ['admin', 'inventory', 'product', 'policy', 'order', 'support'],
}

const AGENT_STATUS = {
  order: 'Looking up your orders',
  shopping: 'Searching the catalogue',
  product: 'Checking product details',
  policy: 'Checking store policy',
  checkout: 'Preparing checkout',
  refund: 'Checking refunds',
  support: 'Working on your request',
  recommendation: 'Finding recommendations',
  inventory: 'Checking inventory',
  admin: 'Working on shop ops',
}

function statusFromEvent(data) {
  const agent = String(data?.agent || '')
  const task = String(data?.task || '').trim()
  const base = AGENT_STATUS[agent] || (agent ? `${agent} is working` : 'Working on that')
  if (task && task.length < 48 && !base.toLowerCase().includes(task.toLowerCase())) {
    return `${base} · ${task}`
  }
  return base
}

export default function ChatWidget(props) {
  const [identity, setIdentity] = useState(() => `${chatActorId()}:${getToken() || ''}`)
  useEffect(() => {
    const sync = () => setIdentity(`${chatActorId()}:${getToken() || ''}`)
    window.addEventListener('storage', sync)
    window.addEventListener('shopzone-store', sync)
    return () => {
      window.removeEventListener('storage', sync)
      window.removeEventListener('shopzone-store', sync)
    }
  }, [])
  return <ChatSession key={`${identity}:${props.persona}:${props.productContext?.id}:${props.mode}:${props.promptVersion}`} {...props} />
}

function ChatSession({
  persona = 'customer',
  productContext = null,
  forceHide = false,
  mode = 'multi_agent',
  promptVersion = 'v1',
}) {
  const loggedIn = isLoggedIn()
  const name = typeof window !== 'undefined' ? (localStorage.getItem('customer_name') || '') : ''
  const actorId = typeof window !== 'undefined' ? chatActorId() : 'guest'
  const cfg = useMemo(() => welcomeFor(persona, loggedIn, name), [persona, loggedIn, name])
  const storageKey = `sz-chat-${persona}-${productContext?.id || 'global'}-${actorId}`
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [status, setStatus] = useState(null)
  const [saved] = useState(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(storageKey) || 'null')
      if (value?.actor === actorId && Array.isArray(value.msgs) && value.msgs.length && value.msgs.every((m) => m && ['bot', 'user'].includes(m.role) && typeof m.text === 'string')) return value
    } catch { /* Start fresh when storage is unavailable or invalid. */ }
    return null
  })
  const [msgs, setMsgs] = useState(() => saved?.msgs || [{ role: 'bot', text: cfg.welcome }])
  const [conv, setConv] = useState(() => typeof saved?.conv === 'string' ? saved.conv : null)
  const [suggests, setSuggests] = useState(() => Array.isArray(saved?.suggests) && saved.suggests.every((s) => typeof s === 'string') ? saved.suggests : cfg.suggests)
  const [liveAgents, setLiveAgents] = useState([])
  const [lastMeta, setLastMeta] = useState(null)
  const [busy, setBusy] = useState(false)
  const [pendingStatus, setPendingStatus] = useState('Working on that')
  const endRef = useRef(null)
  const sendRef = useRef(() => {})
  const inflightRef = useRef(false)
  const inputRef = useRef('')

  const requestRef = useRef(null)
  useEffect(() => () => requestRef.current?.abort(), [])

  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify({ actor: actorId, msgs, conv, suggests })) } catch { /* Chat works when storage is full or unavailable. */ }
  }, [msgs, conv, suggests, storageKey, actorId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs, open, busy, pendingStatus])

  useEffect(() => {
    fetchAgentStatus().then(setStatus)
  }, [])

  useEffect(() => {
    const timers = new Set()
    const onAsk = (e) => {
      const d = e.detail || {}
      if (d.persona && d.persona !== persona) return
      setOpen(true)
      if (d.text) timers.add(setTimeout(() => sendRef.current(d.text), 80))
    }
    window.addEventListener('shopzone-ask-agent', onAsk)
    return () => {
      window.removeEventListener('shopzone-ask-agent', onAsk)
      timers.forEach(clearTimeout)
    }
  }, [persona])

  const liveLabel = useMemo(() => {
    if (!status) return 'Connecting…'
    if (status.unavailable) return 'Connection unavailable · try again shortly'
    if (status.llm_live) return `Live · ${status.llm_backend} · ${status.llm_model}`
    return 'Demo mock · agents still place orders after you sign in'
  }, [status])

  useEffect(() => { sendRef.current = send })

  if (forceHide) return null
  if (persona !== 'owner' && typeof window !== 'undefined' && window.location.pathname.includes('/admin') && isAdmin()) {
    return null
  }

  function newChat() {
    if (inflightRef.current) return
    setConv(null)
    setMsgs([{ role: 'bot', text: cfg.welcome }])
    setSuggests(cfg.suggests)
    setLastMeta(null)
    setLiveAgents([])
    setDraft('')
    try { sessionStorage.removeItem(storageKey) } catch { /* optional persistence */ }
  }

  function setDraft(value) {
    inputRef.current = value
    setInput(value)
  }

  async function send(text) {
    const raw = String(text != null && text !== '' ? text : inputRef.current || input).trim()
    if (!raw || inflightRef.current) return
    if (persona === 'customer' && !loggedIn && /sign in/i.test(raw) && raw.length < 40) {
      window.location.assign('/shop/login')
      return
    }
    const request = new AbortController()
    requestRef.current = request
    inflightRef.current = true
    setBusy(true)
    setDraft('')
    setLiveAgents([])
    setPendingStatus('Working on that')
    setMsgs((m) => [...m, { role: 'user', text: raw }])
    try {
      const final = await chatStream(raw, conv, (ev) => {
        if (request.signal.aborted) return
        if (ev.type === 'agent' && ev.data?.type === 'agent_start') {
          setLiveAgents((a) => Array.from(new Set([...a, ev.data.agent])))
          setPendingStatus(statusFromEvent(ev.data))
        }
        if (ev.type === 'agent' && ev.data?.type === 'graph_start') {
          const team = (ev.data.specialists || []).filter(Boolean).join(', ')
          setPendingStatus(team ? `Agents working · ${team}` : 'ShopZone team is working')
        }
        if (ev.type === 'error') {
          throw new Error(ev.data?.message || 'Chat failed')
        }
      }, { persona, productId: productContext?.id, mode, promptVersion, signal: request.signal })
      if (request.signal.aborted) return
      if (final?.conversation_id) setConv(final.conversation_id)
      if (final?.suggestions?.length) setSuggests(final.suggestions)
      setLastMeta({
        framework: final?.framework || mode,
        mode: final?.mode || mode,
        terminal: final?.terminal_state,
        trace: final?.trace_id,
        run: final?.run_id,
        approval: final?.approval_id,
        agents: (final?.sub_results || []).map((s) => s.agent).filter(Boolean),
      })
      const rawAnswer = String(final?.answer || '').trim()
      const sub = (final?.sub_results || [])
        .filter((s) => s?.agent && s.agent !== 'react')
        .map((s) => {
          const summary = String(s.summary || '').trim()
          if (!summary || rawAnswer.startsWith(summary.slice(0, 48))) return ''
          return `• ${s.agent}: ${summary.slice(0, 140)}`
        })
        .filter(Boolean)
        .join('\n')
      const cites = (final?.citations || []).slice(0, 4)
      const citeLine = cites.length ? `\n\nSources: ${cites.join(', ')}` : ''
      const answer = (rawAnswer || 'I could not finish that reply. Try once more — search, return policy, or your orders.') + (sub ? `\n\n${sub}` : '') + citeLine
      setMsgs((m) => [...m, { role: 'bot', text: answer }])
    } catch (e) {
      if (request.signal.aborted) return
      setMsgs((m) => [...m, { role: 'bot', text: String(e.message || e) }])
    } finally {
      inflightRef.current = false
      setBusy(false)
      setLiveAgents([])
    }
  }


  if (!open) {
    return (
      <button
        type="button"
        className={`sz-chat-fab ${persona === 'owner' ? 'owner' : ''}`}
        data-testid={`chat-fab-${persona}`}
        aria-label={`Open ${cfg.title}`}
        onClick={() => setOpen(true)}
      >
        {persona === 'owner' ? '⚙' : '✦'}
      </button>
    )
  }

  return (
    <>
      <button type="button" className={`sz-chat-fab ${persona === 'owner' ? 'owner' : ''}`} data-testid={`chat-fab-close-${persona}`} onClick={() => setOpen(false)}>×</button>
      <div className="sz-chat-panel" data-testid={`chat-panel-${persona}`} role="dialog" aria-label={cfg.title}>
        <div className="sz-chat-head">
          <div className="sz-chat-head-row">
            <strong>{cfg.title}</strong>
            <button type="button" className="sz-chat-new" data-testid={`chat-new-${persona}`} disabled={busy} onClick={newChat}>New chat</button>
          </div>
          <div style={{ fontSize: 11, opacity: 0.7 }}>{cfg.sub}</div>
          <div className={`sz-chat-live ${status?.llm_live ? 'on' : 'off'}`} data-testid={`chat-live-${persona}`}>{liveLabel}</div>
          {lastMeta && (
            <div className="sz-chat-meta" data-testid={`chat-meta-${persona}`}>
              {lastMeta.framework} · {lastMeta.mode}{lastMeta.terminal ? ` · ${lastMeta.terminal}` : ''}
              {lastMeta.trace ? ` · ${lastMeta.trace}` : ''}
              {lastMeta.approval ? ` · HITL ${lastMeta.approval}` : ''}
            </div>
          )}
        </div>
        <div className="sz-agent-chips" data-testid={`chat-agents-${persona}`}>
          {(TEAM_AGENTS[persona] || TEAM_AGENTS.customer).map((a) => (
            <span
              key={a}
              className={`sz-agent-chip ${liveAgents.includes(a) ? 'live' : ''} ${lastMeta?.agents?.includes(a) ? 'on' : ''}`}
            >
              {a}
            </span>
          ))}
        </div>
        <div className="sz-chat-msgs" data-testid={`chat-messages-${persona}`}>
          {msgs.map((m, i) => (
            <div key={i} className={`sz-msg ${m.role}`}>{m.text}</div>
          ))}
          {busy && (
            <div className="sz-msg bot sz-msg-pending" data-testid={`chat-pending-${persona}`} aria-live="polite" aria-busy="true">
              <span className="sz-typing" aria-hidden="true"><i /><i /><i /></span>
              <span className="sz-pending-copy">
                <span className="sz-pending-label">{pendingStatus}</span>
                <span className="sz-pending-hint">Processing… one request at a time</span>
              </span>
            </div>
          )}
          <div ref={endRef} />
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '0 10px 8px' }}>
          {suggests.map((s) => (
            <button key={s} type="button" className="sz-btn sz-btn-ghost" style={{ padding: '6px 10px', fontSize: 11 }} data-testid={`chat-suggest-${persona}`} disabled={busy} onClick={() => send(s)}>{s}</button>
          ))}
        </div>
        <form
          className="sz-chat-input"
          onSubmit={(e) => {
            e.preventDefault()
            e.stopPropagation()
            send()
          }}
        >
          <input
            data-testid={`chat-input-${persona}`}
            value={input}
            aria-label="Message the assistant"
            maxLength={8000}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={busy ? 'Waiting for the current reply…' : 'Ask anything — type and press Enter'}
            disabled={busy}
            autoComplete="off"
          />
          <button type="submit" className="sz-btn sz-btn-blue" data-testid={`chat-send-${persona}`} disabled={busy}>{busy ? '…' : 'Send'}</button>
        </form>
      </div>
    </>
  )
}
