import { useEffect, useMemo, useRef, useState } from 'react'
import { chatStream, fetchAgentStatus, isAdmin, isLoggedIn } from '../api'

function customerCopy(loggedIn, name) {
  if (loggedIn) {
    return {
      title: 'ShopZone Assistant',
      sub: 'Your personal shopper · orders, checkout, payments',
      welcome: `Hi${name ? ` ${name}` : ''}! I’m your logged-in agent. I can show your orders, check payment status, and place items you name after you pick UPI, Card, or Cash on delivery.`,
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
    sub: 'Guest · sign in to order',
    welcome: 'Hi! I can search the catalogue and explain UPI / Card / COD. Sign in so I can show your orders and place items for you.',
    suggests: [
      'Laptops under 80000 with 16GB RAM',
      'What payment methods can I use?',
      'What is your return policy?',
      'Sign in to place an order',
    ],
  }
}

const PERSONAS = {
  product: {
    title: 'Product Specialist',
    sub: 'This product · stock, warranty, buy',
    welcome: 'Ask about this product’s specs, stock, warranty, or say “buy this with UPI” after you sign in.',
    suggests: [
      'Compare with similar products',
      'Check inventory for this item',
      'What payment methods can I use?',
      'Buy this with UPI',
    ],
  },
  owner: {
    title: 'Seller Ops Concierge',
    sub: 'Owner agents · orders, inventory, policy',
    welcome: 'Owner multi-agent desk: pending orders, approve/reject, low stock, and ops policy.',
    suggests: [
      'List pending orders',
      'Approve the oldest pending order',
      'Which products need restock?',
      'Summarize today’s order queue',
    ],
  },
}

export default function ChatWidget({
  persona = 'customer',
  productContext = null,
  forceHide = false,
}) {
  const [loggedIn, setLoggedIn] = useState(() => isLoggedIn())
  const name = typeof window !== 'undefined' ? (localStorage.getItem('customer_name') || '') : ''
  const cfg = persona === 'customer'
    ? customerCopy(loggedIn, name)
    : (PERSONAS[persona] || customerCopy(loggedIn, name))
  const storageKey = `sz-chat-${persona}-${productContext?.id || 'global'}-${loggedIn ? 'in' : 'out'}`
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [status, setStatus] = useState(null)
  const [msgs, setMsgs] = useState(() => [{ role: 'bot', text: cfg.welcome }])
  const [conv, setConv] = useState(null)
  const endRef = useRef(null)

  useEffect(() => {
    const sync = () => setLoggedIn(isLoggedIn())
    window.addEventListener('storage', sync)
    window.addEventListener('shopzone-store', sync)
    return () => {
      window.removeEventListener('storage', sync)
      window.removeEventListener('shopzone-store', sync)
    }
  }, [])

  useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) || 'null')
      if (saved?.msgs?.length) {
        setMsgs(saved.msgs)
        setConv(saved.conv || null)
      } else {
        setMsgs([{ role: 'bot', text: cfg.welcome }])
        setConv(null)
      }
    } catch {
      setMsgs([{ role: 'bot', text: cfg.welcome }])
    }
  }, [storageKey, cfg.welcome])

  useEffect(() => {
    sessionStorage.setItem(storageKey, JSON.stringify({ msgs, conv }))
  }, [msgs, conv, storageKey])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs, open])

  useEffect(() => {
    fetchAgentStatus().then(setStatus)
  }, [])

  const liveLabel = useMemo(() => {
    if (!status) return 'Connecting…'
    if (status.llm_live) return `Live · ${status.llm_backend} · ${status.llm_model}`
    return 'Demo mock · agents still place orders after you sign in'
  }, [status])

  if (forceHide) return null
  if (persona !== 'owner' && typeof window !== 'undefined' && window.location.pathname.includes('/admin') && isAdmin()) {
    return null
  }

  async function send(text) {
    const raw = (text || input).trim()
    if (!raw) return
    if (persona === 'customer' && !loggedIn && /sign in/i.test(raw)) {
      window.location.href = '/shop/login'
      return
    }
    setInput('')
    setMsgs((m) => [...m, { role: 'user', text: raw }, { role: 'bot', text: `${cfg.title} working…` }])
    try {
      const final = await chatStream(raw, conv, (ev) => {
        if (ev.type === 'agent' && ev.data?.type === 'agent_start') {
          setMsgs((m) => {
            const copy = [...m]
            copy[copy.length - 1] = { role: 'bot', text: `${ev.data.agent} running…` }
            return copy
          })
        }
        if (ev.type === 'agent' && ev.data?.type === 'graph_start') {
          setMsgs((m) => {
            const copy = [...m]
            copy[copy.length - 1] = { role: 'bot', text: `${persona} team: ${(ev.data.specialists || []).join(', ')}` }
            return copy
          })
        }
      }, { persona, productId: productContext?.id })
      if (final?.conversation_id) setConv(final.conversation_id)
      const sub = (final?.sub_results || []).map((s) => `• ${s.agent}: ${String(s.summary || '').slice(0, 140)}`).join('\n')
      const cites = (final?.citations || []).slice(0, 4)
      const citeLine = cites.length ? `\n\nSources: ${cites.join(', ')}` : ''
      const answer = (final?.answer || 'No answer') + (sub ? `\n\n${sub}` : '') + citeLine
      setMsgs((m) => {
        const copy = [...m]
        copy[copy.length - 1] = { role: 'bot', text: answer }
        return copy
      })
    } catch (e) {
      setMsgs((m) => {
        const copy = [...m]
        copy[copy.length - 1] = { role: 'bot', text: String(e.message || e) }
        return copy
      })
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
      <div className="sz-chat-panel" data-testid={`chat-panel-${persona}`} role="dialog">
        <div className="sz-chat-head">
          <strong>{cfg.title}</strong>
          <div style={{ fontSize: 11, opacity: 0.7 }}>{cfg.sub}</div>
          <div className={`sz-chat-live ${status?.llm_live ? 'on' : 'off'}`} data-testid={`chat-live-${persona}`}>{liveLabel}</div>
        </div>
        <div className="sz-chat-msgs" data-testid={`chat-messages-${persona}`}>
          {msgs.map((m, i) => (
            <div key={i} className={`sz-msg ${m.role}`}>{m.text}</div>
          ))}
          <div ref={endRef} />
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '0 10px 8px' }}>
          {cfg.suggests.map((s) => (
            <button key={s} type="button" className="sz-btn sz-btn-ghost" style={{ padding: '6px 10px', fontSize: 11 }} data-testid={`chat-suggest-${persona}`} onClick={() => send(s)}>{s}</button>
          ))}
        </div>
        <div className="sz-chat-input">
          <input data-testid={`chat-input-${persona}`} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && send()} placeholder="Ask your agent…" />
          <button type="button" className="sz-btn sz-btn-blue" data-testid={`chat-send-${persona}`} onClick={() => send()}>Send</button>
        </div>
      </div>
    </>
  )
}
