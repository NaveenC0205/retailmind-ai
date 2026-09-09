import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { authHeaders, askAgent, decideApproval, fetchApprovals, fetchRuns, formatPrice, isAdmin, isLoggedIn } from '../api'
import { downloadText } from '../store'
import ChatWidget from '../components/ChatWidget'
import Loading from '../components/Loading'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'products', label: 'Products' },
  { id: 'orders', label: 'Orders' },
  { id: 'inventory', label: 'Inventory' },
  { id: 'approvals', label: 'HITL approvals' },
  { id: 'agents', label: 'Agent runs' },
  { id: 'preview', label: 'Live preview' },
]

const emptyForm = {
  sku: '',
  title: '',
  brand: '',
  category: 'phones',
  price_inr: 9999,
  rating: 4.5,
  description: '',
}

export default function AdminPage() {
  const nav = useNavigate()
  const [tab, setTab] = useState('overview')
  const [loading, setLoading] = useState(true)
  const [products, setProducts] = useState([])
  const [orders, setOrders] = useState([])
  const [inventory, setInventory] = useState([])
  const [orderFilter, setOrderFilter] = useState('all')
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState(emptyForm)
  const [editId, setEditId] = useState(null)
  const [busy, setBusy] = useState(false)
  const [approvals, setApprovals] = useState([])
  const [runs, setRuns] = useState([])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [pRes, oRes, iRes] = await Promise.all([
        fetch('/api/admin/products?limit=500', { headers: authHeaders(false) }),
        fetch('/api/admin/orders?limit=200', { headers: authHeaders(false) }),
        fetch('/api/admin/inventory', { headers: authHeaders(false) }),
      ])
      if (![pRes, oRes, iRes].every((response) => response.ok)) throw new Error('Dashboard request failed')
      const pData = await pRes.json()
      const oData = await oRes.json()
      const iData = await iRes.json()
      setProducts(pData.products || [])
      setOrders(oData.orders || [])
      setInventory(iData.inventory || iData.items || [])
      try {
        const [aData, rData] = await Promise.all([
          fetchApprovals('pending'),
          fetchRuns(20),
        ])
        setApprovals(aData.approvals || [])
        setRuns(rData.runs || [])
      } catch {
        setApprovals([])
        setRuns([])
      }
    } catch {
      setMsg('Failed to load dashboard data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!isLoggedIn() || !isAdmin()) {
      nav('/login?redirect=/shop/admin')
      return
    }
    refresh()
  }, [nav, refresh])

  async function saveProduct(e) {
    e.preventDefault()
    setBusy(true)
    setMsg('')
    try {
      const url = editId ? `/api/admin/products/${editId}` : '/api/admin/products'
      const method = editId ? 'PUT' : 'POST'
      const body = editId
        ? {
            title: form.title,
            brand: form.brand,
            category: form.category,
            price_inr: Number(form.price_inr),
            rating: Number(form.rating),
            description: form.description,
          }
        : {
            sku: form.sku || `SKU-${Date.now()}`,
            title: form.title,
            brand: form.brand,
            category: form.category,
            price_inr: Number(form.price_inr),
            rating: Number(form.rating),
            description: form.description,
            attributes: {},
          }
      const res = await fetch(url, { method, headers: authHeaders(), body: JSON.stringify(body) })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Save failed')
      setMsg(editId ? `Updated ${data.id} — live on storefront` : `Created ${data.id} — live on storefront`)
      setForm(emptyForm)
      setEditId(null)
      await refresh()
    } catch (err) {
      setMsg(err.message)
    } finally {
      setBusy(false)
    }
  }

  function startEdit(p) {
    setEditId(p.id)
    setForm({
      sku: p.sku,
      title: p.title,
      brand: p.brand,
      category: p.category,
      price_inr: p.price_inr,
      rating: p.rating,
      description: p.description || '',
    })
    setTab('products')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  async function removeProduct(id) {
    if (!confirm(`Delete product ${id}?`)) return
    const res = await fetch(`/api/admin/products/${id}`, { method: 'DELETE', headers: authHeaders(false) })
    const data = await res.json()
    if (!res.ok) {
      setMsg(data.detail || 'Delete failed')
      return
    }
    setMsg(`Deleted ${id}`)
    await refresh()
  }

  async function updateOrder(id, status) {
    const res = await fetch(`/api/admin/orders/${id}/status`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ status }),
    })
    const data = await res.json()
    if (!res.ok) {
      setMsg(data.detail || 'Update failed')
      return
    }
    setMsg(`Order ${id} → ${status}`)
    setOrders((prev) => prev.map((o) => (o.id === id ? { ...o, status } : o)))
  }

  async function bumpStock(productId, change) {
    const res = await fetch(`/api/admin/inventory/${productId}`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ qty_change: change, reason: 'seller_dashboard' }),
    })
    const data = await res.json()
    if (!res.ok) {
      setMsg(data.detail || 'Inventory update failed')
      return
    }
    setMsg(`Stock updated for ${productId}`)
    await refresh()
  }

  const filteredOrders = orderFilter === 'all' ? orders : orders.filter((o) => o.status === orderFilter)

  if (loading) return <Loading label="Loading seller dashboard…" testId="admin-loading" />

  return (
    <div className="sz-admin-shell" data-testid="admin-page">
      <aside className="sz-admin-side" data-testid="admin-sidebar">
        <div className="sz-admin-brand">Seller Central</div>
        <p className="sz-meta" style={{ padding: '0 16px 12px' }}>Wide menu · live catalogue sync</p>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`sz-side-link ${tab === t.id ? 'active' : ''}`}
            data-testid={`admin-tab-${t.id}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
        <Link to="/store" className="sz-side-link" target="_blank" rel="noreferrer">Storefront ↗</Link>
        <Link to="/lab" className="sz-side-link" data-testid="admin-open-lab">Framework lab</Link>
        <Link to="/" className="sz-side-link">Exit to shop</Link>
      </aside>

      <div className="sz-admin-main">
        <h1 style={{ fontSize: 32, fontWeight: 600, marginBottom: 4 }}>{TABS.find((t) => t.id === tab)?.label}</h1>
        <p className="sz-meta">Product uploads & order updates reflect on the customer website.</p>
        {msg && <p data-testid="admin-msg" style={{ background: '#eef6ff', padding: 12, borderRadius: 12 }}>{msg}</p>}

      {tab === 'overview' && (
        <section data-testid="admin-overview" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: 12 }}>
          <div style={{ background: '#fff', borderRadius: 16, padding: 18 }}><div className="sz-meta">Products</div><div style={{ fontSize: 32, fontWeight: 600 }}>{products.length}</div></div>
          <div style={{ background: '#fff', borderRadius: 16, padding: 18 }}><div className="sz-meta">Orders</div><div style={{ fontSize: 32, fontWeight: 600 }}>{orders.length}</div></div>
          <div style={{ background: '#fff', borderRadius: 16, padding: 18 }}><div className="sz-meta">Pending</div><div style={{ fontSize: 32, fontWeight: 600 }}>{orders.filter((o) => o.status === 'placed').length}</div></div>
          <div style={{ background: '#fff', borderRadius: 16, padding: 18 }}><div className="sz-meta">Inventory rows</div><div style={{ fontSize: 32, fontWeight: 600 }}>{inventory.length}</div></div>
          <div style={{ background: '#fff', borderRadius: 16, padding: 18 }}><div className="sz-meta">HITL pending</div><div style={{ fontSize: 32, fontWeight: 600 }}>{approvals.length}</div></div>
          <div style={{ gridColumn: '1 / -1' }}>
            <Link to="/store" className="sz-btn sz-btn-blue" target="_blank" rel="noreferrer">Open storefront (new tab)</Link>
            <button type="button" className="sz-btn sz-btn-ghost" style={{ marginLeft: 8 }} onClick={() => downloadText('catalogue.json', JSON.stringify(products, null, 2), 'application/json')}>Download catalogue</button>
            <button type="button" className="sz-btn sz-btn-dark" style={{ marginLeft: 8 }} data-testid="admin-ask-pending" onClick={() => askAgent('List pending orders', { persona: 'owner' })}>Ask agent: pending orders</button>
            <button type="button" className="sz-btn sz-btn-ghost" style={{ marginLeft: 8 }} onClick={() => askAgent('Which products need restock?', { persona: 'owner' })}>Ask agent: low stock</button>
          </div>
        </section>
      )}

      {tab === 'products' && (
        <section data-testid="admin-products">
          <form onSubmit={saveProduct} style={{ background: '#fff', borderRadius: 18, padding: 20, marginBottom: 20 }} data-testid="product-form">
            <h2 style={{ marginTop: 0 }}>{editId ? `Edit ${editId}` : 'Add / upload product'}</h2>
            <p className="sz-meta">Saved products appear immediately on /shop/store and search.</p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              {!editId && (
                <div><label className="sz-meta">SKU</label><input className="sz-input" data-testid="prod-sku" value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} placeholder="AUTO if empty" /></div>
              )}
              <div><label className="sz-meta">Title</label><input className="sz-input" data-testid="prod-title" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
              <div><label className="sz-meta">Brand</label><input className="sz-input" data-testid="prod-brand" required value={form.brand} onChange={(e) => setForm({ ...form, brand: e.target.value })} /></div>
              <div>
                <label className="sz-meta">Category</label>
                <select className="sz-input" data-testid="prod-category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                  {['phones', 'laptops', 'audio', 'monitors', 'accessories'].map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <div><label className="sz-meta">Price ₹</label><input className="sz-input" data-testid="prod-price" type="number" required value={form.price_inr} onChange={(e) => setForm({ ...form, price_inr: e.target.value })} /></div>
              <div><label className="sz-meta">Rating</label><input className="sz-input" data-testid="prod-rating" type="number" step="0.1" min="0" max="5" value={form.rating} onChange={(e) => setForm({ ...form, rating: e.target.value })} /></div>
            </div>
            <label className="sz-meta">Description</label>
            <textarea className="sz-input" data-testid="prod-desc" rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button type="submit" className="sz-btn sz-btn-blue" data-testid="prod-save" disabled={busy}>{busy ? 'Saving…' : (editId ? 'Update product' : 'Create product')}</button>
              {editId && <button type="button" className="sz-btn sz-btn-ghost" onClick={() => { setEditId(null); setForm(emptyForm) }}>Cancel edit</button>}
            </div>
          </form>

          <div className="sz-grid">
            {products.map((p) => (
              <div key={p.id} className="sz-card" data-testid={`admin-product-${p.id}`}>
                <div className="sz-card-body">
                  <div className="sz-meta">{p.category} · {p.id}</div>
                  <div className="sz-card-title">{p.title}</div>
                  <div className="sz-meta">{p.brand} · ★ {p.rating}</div>
                  <div className="sz-price">{formatPrice(p.price_inr)}</div>
                  <div className="sz-card-actions" style={{ gridTemplateColumns: '1fr 1fr 1fr 1fr' }}>
                    <button type="button" className="sz-btn sz-btn-blue" data-testid={`edit-${p.id}`} onClick={() => startEdit(p)}>Edit</button>
                    <Link to={`/product/${p.id}`} className="sz-btn sz-btn-ghost" data-testid={`view-${p.id}`}>View</Link>
                    <button type="button" className="sz-btn sz-btn-ghost" onClick={() => askAgent(`Check inventory for ${p.title} ${p.id}`, { persona: 'owner' })}>Ask agent</button>
                    <button type="button" className="sz-btn sz-btn-dark" data-testid={`delete-${p.id}`} onClick={() => removeProduct(p.id)}>Delete</button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'orders' && (
        <section data-testid="admin-orders">
          <div className="sz-tabs">
            {['all', 'placed', 'packed', 'shipped', 'delivered', 'cancelled'].map((s) => (
              <button key={s} type="button" className={`sz-tab ${orderFilter === s ? 'active' : ''}`} data-testid={`admin-order-filter-${s}`} onClick={() => setOrderFilter(s)}>{s}</button>
            ))}
          </div>
          <div style={{ display: 'grid', gap: 10 }}>
            {filteredOrders.map((o) => (
              <div key={o.id} style={{ background: '#fff', borderRadius: 16, padding: 14 }} data-testid={`admin-order-${o.id}`}>
                <strong>{o.id}</strong> · {o.customer_id} · <span style={{ textTransform: 'capitalize' }}>{o.status}</span> · {formatPrice(o.total_inr)}
                <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                  <button type="button" className="sz-btn sz-btn-blue" onClick={() => updateOrder(o.id, 'packed')}>Packed</button>
                  <button type="button" className="sz-btn sz-btn-dark" onClick={() => updateOrder(o.id, 'shipped')}>Shipped</button>
                  <button type="button" className="sz-btn sz-btn-ghost" onClick={() => updateOrder(o.id, 'delivered')}>Delivered</button>
                  <button type="button" className="sz-btn sz-btn-ghost" onClick={() => updateOrder(o.id, 'cancelled')}>Cancel</button>
                  <button type="button" className="sz-btn sz-btn-ghost" data-testid={`ask-approve-${o.id}`} onClick={() => askAgent(`Approve order ${o.id}`, { persona: 'owner' })}>Ask agent: approve</button>
                  <Link to={`/orders/${o.id}`} className="sz-btn sz-btn-ghost">Details</Link>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'inventory' && (
        <section data-testid="admin-inventory">
          <div style={{ display: 'grid', gap: 8 }}>
            {(inventory.length ? inventory : []).map((row, i) => (
              <div key={`${row.product_id}-${row.warehouse}-${i}`} style={{ background: '#fff', borderRadius: 12, padding: 12, display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', alignItems: 'center' }} data-testid={`inv-${row.product_id}`}>
                <div>
                  <strong>{row.product_title || row.title || row.product_id}</strong>
                  <div className="sz-meta">{row.product_id} · {row.warehouse || 'BLR-1'} · qty {row.qty_available}</div>
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button type="button" className="sz-btn sz-btn-blue" onClick={() => bumpStock(row.product_id, 10)}>+10</button>
                  <button type="button" className="sz-btn sz-btn-ghost" onClick={() => bumpStock(row.product_id, -5)}>-5</button>
                  <button type="button" className="sz-btn sz-btn-ghost" onClick={() => askAgent(`Check inventory for ${row.product_title || row.product_id}`, { persona: 'owner' })}>Ask agent</button>
                </div>
              </div>
            ))}
            {!inventory.length && <p className="sz-meta">No inventory rows yet.</p>}
          </div>
        </section>
      )}

      {tab === 'approvals' && (
        <section data-testid="admin-approvals">
          <p className="sz-meta">Human-in-the-loop pauses from high-value customer actions. Approve or reject here.</p>
          {!approvals.length && <p data-testid="approvals-empty">No pending approvals.</p>}
          <div style={{ display: 'grid', gap: 10 }}>
            {approvals.map((a) => (
              <div key={a.id} style={{ background: '#fff', borderRadius: 16, padding: 14 }} data-testid={`approval-${a.id}`}>
                <strong>{a.id}</strong> · {a.tool} · {a.customer_id}
                <div className="sz-meta">{a.reason}</div>
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <button type="button" className="sz-btn sz-btn-blue" onClick={async () => {
                    try {
                      const out = await decideApproval(a.id, 'approve')
                      setMsg(`Approved ${a.id} · executed=${out.executed}`)
                      await refresh()
                    } catch (e) { setMsg(e.message) }
                  }}>Approve</button>
                  <button type="button" className="sz-btn sz-btn-ghost" onClick={async () => {
                    try {
                      await decideApproval(a.id, 'reject')
                      setMsg(`Rejected ${a.id}`)
                      await refresh()
                    } catch (e) { setMsg(e.message) }
                  }}>Reject</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'agents' && (
        <section data-testid="admin-agents">
          <p className="sz-meta">Recent orchestrator runs — LangGraph, RAG, and single-agent. Open Lab to switch frameworks.</p>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <button type="button" className="sz-btn sz-btn-blue" onClick={() => askAgent('List pending orders', { persona: 'owner' })}>Pending queue</button>
            <button type="button" className="sz-btn sz-btn-ghost" onClick={() => askAgent('Approve the oldest pending order', { persona: 'owner' })}>Approve oldest</button>
            <button type="button" className="sz-btn sz-btn-ghost" onClick={() => askAgent('Which products need restock?', { persona: 'owner' })}>Low stock</button>
            <Link className="sz-btn sz-btn-dark" to="/lab">Open framework lab</Link>
          </div>
          {!runs.length && <p>No agent runs yet. Use the seller chatbot or Lab.</p>}
          <div style={{ display: 'grid', gap: 8 }}>
            {runs.map((r) => (
              <div key={r.run_id} style={{ background: '#fff', borderRadius: 12, padding: 12 }} data-testid={`run-${r.run_id}`}>
                <strong>{r.run_id}</strong> · {r.mode} · {r.entry_agent} · {r.terminal_state} · {r.steps} steps · {r.latency_ms}ms
                <div className="sz-meta">
                  {r.trace_id && <a href={`/api/traces/${r.trace_id}`} target="_blank" rel="noreferrer">trace</a>}
                  {' · '}
                  <a href={`/api/runs/${r.run_id}`} target="_blank" rel="noreferrer">tools JSON</a>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'preview' && (
        <section data-testid="admin-preview">
          <h2>Embedded storefront preview</h2>
          <p className="sz-meta">IFrame of live store — reload after product upload.</p>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <button type="button" className="sz-btn sz-btn-blue" data-testid="preview-reload" onClick={() => {
              const el = document.getElementById('seller-preview-frame')
              if (el) el.src = '/shop/store'
            }}>Reload preview</button>
            <a className="sz-btn sz-btn-ghost" href="/shop/store" target="_blank" rel="noreferrer">Open in new tab</a>
          </div>
          <iframe id="seller-preview-frame" className="sz-lab-iframe" title="store-preview" data-testid="seller-preview-iframe" src="/shop/store" />
          <iframe className="sz-lab-iframe" style={{ marginTop: 12, minHeight: 240 }} title="api-preview" data-testid="seller-api-iframe" src="/api/products?limit=5" />
        </section>
      )}

      <ChatWidget persona="owner" />
      </div>
    </div>
  )
}
