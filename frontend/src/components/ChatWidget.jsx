import { useEffect, useMemo, useRef, useState } from 'react'
import { chatStream, isAdmin } from '../api'

const PERSONAS = {
  customer: {
    title: 'ShopZone Assistant',
    sub: 'Customer multi-agent · shopping & orders',
    welcome: 'Hi! I help shoppers find phones, laptops, compare specs, track orders, and check return policy.',
    suggests: [
      'Laptops under 80000 with 16GB RAM',
      'Compare phones rating 4.5+',
      'What is your return policy?',
      'Track my latest order',
    ],
  },
  product: {
    title: 'Product Specialist',
    sub: 'Customer agent · this product page',
    welcome: 'Ask about this product’s specs, similar items, stock, or whether it fits your budget.',
    suggests: [
      'Compare with similar products',
      'Is this good under my budget?',
      'Check inventory for this item',
      'What are key features?',
    ],
  },
  owner: {
    title: 'Seller Ops Concierge',
    sub: 'Owner multi-agent · orders & catalogue',
    welcome: 'Owner mode: list pending orders, approve/reject, and ask about catalogue or inventory ops.',
    suggests: [
      'List pending orders',
      'Approve the oldest pending order',
      'Summarize today’s order queue',
      'Which products need restock?',
    ],
  },
}

export default function ChatWidget({
  persona = 'customer',
  productContext = null,
  forceHide = false,
}) {
  const cfg = PERSONAS[persona] || PERSONAS.customer
  const storageKey = `sz-chat-${persona}-${productContext?.id || 'global'}`
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [msgs, setMsgs] = useState(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) || 'null')
      if (saved?.msgs?.length) return saved.msgs
    } catch { /* ignore */ }
    return [{ role: 'bot', text: cfg.welcome }]
  })
  const [conv, setConv] = useState(() => {
    try {
      return JSON.parse(sessionStorage.getItem(storageKey) || 'null')?.conv || null
    } catch { return null }
  })
  const endRef = useRef(null)

  useEffect(() => {
    sessionStorage.setItem(storageKey, JSON.stringify({ msgs, conv }))
  }, [msgs, conv, storageKey])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs, open])

  const prefix = useMemo(() => {
    if (persona === 'product' && productContext) {
      return `[Product context: ${productContext.id} ${productContext.title} ₹${productContext.price_inr} ${productContext.category}] `
    }
    if (persona === 'owner') return '[Shop owner dashboard] '
    return ''
  }, [persona, productContext])

  if (forceHide) return null
  // Never show customer chat on seller surfaces if somehow mounted
  if (persona !== 'owner' && typeof window !== 'undefined' && window.location.pathname.includes('/admin') && isAdmin()) {
    return null
  }

  async function send(text) {
    const raw = (text || input).trim()
    if (!raw) return
    const message = prefix + raw
    setInput('')
    setMsgs((m) => [...m, { role: 'user', text: raw }, { role: 'bot', text: `${cfg.title} planning…` }])
    try {
      const final = await chatStream(message, conv, (ev) => {
        if (ev.type === 'agent' && ev.data?.type === 'agent_start') {
          setMsgs((m) => {
            const copy = [...m]
            copy[copy.length - 1] = { role: 'bot', text: `${ev.data.agent} running…` }
            return copy
          })
        }
      })
      if (final?.conversation_id) setConv(final.conversation_id)
      const sub = (final?.sub_results || []).map((s) => `• ${s.agent}: ${String(s.summary || '').slice(0, 100)}`).join('\n')
      const answer = (final?.answer || 'No answer') + (sub ? `\n\n${sub}` : '')
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
          <input data-testid={`chat-input-${persona}`} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && send()} placeholder="Ask…" />
          <button type="button" className="sz-btn sz-btn-blue" data-testid={`chat-send-${persona}`} onClick={() => send()}>Send</button>
        </div>
      </div>
    </>
  )
}
