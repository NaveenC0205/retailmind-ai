import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { askAgent, authHeaders, formatPrice, isLoggedIn, productImage } from '../api'
import { downloadText } from '../store'
import Loading from '../components/Loading'

export default function OrderDetailPage() {
  const { id } = useParams()
  const [order, setOrder] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!isLoggedIn()) {
      setErr('Sign in to view order details')
      setLoading(false)
      return
    }
    setLoading(true)
    setErr('')
    fetch(`/api/orders/${id}`, { headers: authHeaders(false) })
      .then(async (r) => {
        const d = await r.json()
        if (!r.ok) throw new Error(d.detail || 'Failed to load order')
        setOrder(d)
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return <Loading label="Loading order details…" testId="order-detail-loading" />
  if (err) {
    return (
      <div className="sz-wrap" data-testid="order-detail-error">
        <p>{err}</p>
        <Link to="/login" className="sz-btn sz-btn-blue">Sign in</Link>
      </div>
    )
  }
  if (!order) return null

  const timeline = order.status_timeline || ['placed', 'packed', 'shipped', 'delivered']
  const idx = order.status_index ?? -1
  const cancelled = ['cancelled', 'returned'].includes(order.status)

  return (
    <div className="sz-wrap" data-testid="order-detail-page">
      <div className="sz-meta"><Link to="/orders">Your Orders</Link> / {order.id}</div>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <h1 style={{ fontSize: 36, fontWeight: 600, margin: '8px 0' }} data-testid="order-detail-id">Order {order.id}</h1>
        <button
          type="button"
          className="sz-btn sz-btn-ghost"
          data-testid="order-download-invoice"
          onClick={() => downloadText(
            `invoice-${order.id}.txt`,
            `ShopZone Invoice\nOrder: ${order.id}\nStatus: ${order.status}\nTotal: ₹${order.total_inr}\nPlaced: ${order.placed_at}\nItems:\n${(order.items || []).map((i) => `- ${i.title} x${i.qty} = ₹${i.line_total_inr}`).join('\n')}\n`,
          )}
        >
          Download invoice
        </button>
        <button type="button" className="sz-btn sz-btn-ghost" data-testid="order-ask-track" onClick={() => askAgent(`Where is my order ${order.id}?`)}>Track with assistant</button>
        <button type="button" className="sz-btn sz-btn-ghost" data-testid="order-ask-return" onClick={() => askAgent(`Can I return items on order ${order.id}?`)}>Ask about return</button>
      </div>
      <p className="sz-meta">Placed {order.placed_at ? new Date(order.placed_at).toLocaleString('en-IN') : '—'} · Channel {order.channel || 'web'}</p>
      <p data-testid="order-detail-status" style={{ fontSize: 18, fontWeight: 600, textTransform: 'capitalize' }}>
        Status: {order.status}
      </p>

      {order.checkout?.address && <section className="sz-lab-card" style={{ marginTop: 20 }}><h2>Delivery details</h2><p>{order.checkout.address}</p><p>Requested date: {order.checkout.delivery_date || 'Standard delivery'}</p>{order.checkout.coupon_code && <p>Coupon: {order.checkout.coupon_code} · Discount: {formatPrice(order.checkout.discount_inr)}</p>}</section>}
      {!cancelled && (
      <div className="sz-timeline" data-testid="order-timeline">
          {timeline.map((step, i) => (
            <div
              key={step}
              className={`sz-timeline-step ${i < idx ? 'done' : ''} ${i === idx ? 'current' : ''}`}
              data-testid={`timeline-${step}`}
            >
              {step}
            </div>
          ))}
        </div>
      )}

      <section style={{ background: '#fff', borderRadius: 18, padding: 20, marginTop: 16 }} data-testid="order-items">
        <h2 style={{ marginTop: 0 }}>Items</h2>
        {(order.items || []).map((i) => (
          <div key={i.product_id} style={{ display: 'grid', gridTemplateColumns: '72px 1fr auto', gap: 12, padding: '12px 0', borderBottom: '1px solid var(--line)' }} data-testid={`order-item-${i.product_id}`}>
            <img src={productImage({ id: i.product_id, category: i.category })} alt="" style={{ width: 72, height: 72, objectFit: 'cover', borderRadius: 10 }} />
            <div>
              <Link to={`/product/${i.product_id}`} className="sz-card-title">{i.title}</Link>
              <div className="sz-meta">{i.brand} · Qty {i.qty}</div>
            </div>
            <strong>{formatPrice(i.line_total_inr)}</strong>
          </div>
        ))}
        <div style={{ textAlign: 'right', marginTop: 12, fontSize: 20, fontWeight: 600 }} data-testid="order-detail-total">
          Order total {formatPrice(order.total_inr)}
        </div>
      </section>

      <section style={{ background: '#fff', borderRadius: 18, padding: 20, marginTop: 16 }} data-testid="order-shipping">
        <h2 style={{ marginTop: 0 }}>Shipping details</h2>
        {!(order.shipping || []).length && <p className="sz-meta">No shipment yet — order is being prepared.</p>}
        {(order.shipping || []).map((s) => (
          <div key={s.shipment_id} data-testid={`shipment-${s.shipment_id}`} style={{ marginBottom: 16 }}>
            <p><strong>{s.carrier}</strong> · AWB <code data-testid="shipment-awb">{s.awb}</code></p>
            <p className="sz-meta">Shipment status: <strong>{s.status}</strong>{s.exception_code ? ` · ${s.exception_code}` : ''}</p>
            {s.promised_at && <p className="sz-meta">Promised: {new Date(s.promised_at).toLocaleString('en-IN')}</p>}
            {s.delivered_at && <p className="sz-meta">Delivered: {new Date(s.delivered_at).toLocaleString('en-IN')}</p>}
            <ul data-testid="shipment-events">
              {(s.events || []).map((e, idx) => (
                <li key={idx}><strong>{e.code}</strong> — {e.message} <span className="sz-meta">{e.at ? new Date(e.at).toLocaleString('en-IN') : ''}</span></li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      <section style={{ background: '#fff', borderRadius: 18, padding: 20, marginTop: 16 }} data-testid="order-payments">
        <h2 style={{ marginTop: 0 }}>Payment</h2>
        {!(order.payments || []).length && <p className="sz-meta">No payment record.</p>}
        {(order.payments || []).map((p) => (
          <p key={p.payment_id} data-testid={`payment-${p.payment_id}`}>
            {p.method?.toUpperCase()} · {formatPrice(p.amount_inr)} · {p.status} · ref {p.gateway_ref}
          </p>
        ))}
      </section>
    </div>
  )
}
