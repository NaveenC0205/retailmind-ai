import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { fetchOrders, formatPrice, isLoggedIn } from '../api'
import { downloadOrdersCsv, downloadText } from '../store'
import Loading from '../components/Loading'

const STATUSES = ['all', 'placed', 'packed', 'shipped', 'delivered', 'cancelled', 'returned']

export default function OrdersPage() {
  const [orders, setOrders] = useState([])
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState('all')
  const [params] = useSearchParams()
  const placed = params.get('placed')

  useEffect(() => {
    if (!isLoggedIn()) {
      setLoading(false)
      return
    }
    setLoading(true)
    fetchOrders()
      .then(setOrders)
      .catch(() => setOrders([]))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    if (status === 'all') return orders
    return orders.filter((o) => o.status === status)
  }, [orders, status])

  if (!isLoggedIn()) {
    return (
      <div className="sz-wrap" data-testid="orders-login">
        <h1>Your Orders</h1>
        <p className="sz-meta">Sign in to see order history, shipping, and invoices.</p>
        <Link to="/login?redirect=/shop/orders" className="sz-btn sz-btn-blue">Sign in</Link>
      </div>
    )
  }

  if (loading) return <Loading label="Loading order history…" testId="orders-loading" />

  return (
    <div className="sz-wrap" data-testid="orders-page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 40, fontWeight: 600, margin: 0 }}>Your Orders</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="button" className="sz-btn sz-btn-ghost" data-testid="download-orders-csv" onClick={() => downloadOrdersCsv(orders)}>Download CSV</button>
          <button type="button" className="sz-btn sz-btn-dark" data-testid="download-orders-json" onClick={() => downloadText('orders.json', JSON.stringify(orders, null, 2), 'application/json')}>Download JSON</button>
        </div>
      </div>

      {placed && (
        <p data-testid="order-placed-banner" style={{ background: '#e8f8ef', padding: 12, borderRadius: 12, marginTop: 16 }}>
          Order placed: <Link to={`/orders/${placed}`}><strong>{placed}</strong></Link>
        </p>
      )}

      <div className="sz-tabs" data-testid="orders-status-tabs" style={{ marginTop: 20 }}>
        {STATUSES.map((s) => (
          <button
            key={s}
            type="button"
            className={`sz-tab ${status === s ? 'active' : ''}`}
            data-testid={`orders-tab-${s}`}
            onClick={() => setStatus(s)}
          >
            {s === 'all' ? `All (${orders.length})` : `${s} (${orders.filter((o) => o.status === s).length})`}
          </button>
        ))}
      </div>

      <div style={{ display: 'grid', gap: 12 }}>
        {!filtered.length && <p data-testid="orders-empty">No orders with this status. <Link to="/store">Continue shopping</Link></p>}
        {filtered.map((o) => (
          <div key={o.id} data-testid={`order-${o.id}`} style={{ background: '#fff', borderRadius: 18, padding: 18 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
              <div><div className="sz-meta">ORDER PLACED</div><strong>{o.placed_at ? new Date(o.placed_at).toLocaleDateString('en-IN') : '—'}</strong></div>
              <div><div className="sz-meta">TOTAL</div><strong>{formatPrice(o.total_inr)}</strong></div>
              <div><div className="sz-meta">ORDER #</div><strong>{o.id}</strong></div>
              <div><div className="sz-meta">STATUS</div><strong data-testid={`order-status-${o.id}`} style={{ textTransform: 'capitalize' }}>{o.status}</strong></div>
            </div>
            <div className="sz-meta" style={{ marginTop: 8 }}>{(o.items || []).length} item(s)</div>
            <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
              <Link to={`/orders/${o.id}`} className="sz-btn sz-btn-blue" data-testid={`view-order-${o.id}`}>View details & shipping</Link>
              <Link to="/store" className="sz-btn sz-btn-ghost">Buy again</Link>
              <button
                type="button"
                className="sz-btn sz-btn-ghost"
                data-testid={`download-invoice-${o.id}`}
                onClick={() => downloadText(`invoice-${o.id}.txt`, `Invoice ${o.id}\nStatus: ${o.status}\nTotal: ${o.total_inr}\n`)}
              >
                Download invoice
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
