import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { fetchProduct, formatPrice, productImage } from '../api'
import { addToCart, toggleWishlist } from '../store'
import ChatWidget from '../components/ChatWidget'
import Loading from '../components/Loading'

export default function ProductPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const [p, setP] = useState(null)
  const [qty, setQty] = useState(1)
  const [err, setErr] = useState('')

  useEffect(() => {
    fetchProduct(id).then(setP).catch((e) => setErr(e.message))
  }, [id])

  if (err) return <div className="sz-wrap" data-testid="product-error">{err}</div>
  if (!p) return <Loading label="Loading product…" testId="product-loading" />

  return (
    <div className="sz-wrap" data-testid="product-page">
      <div className="sz-meta" style={{ marginBottom: 12 }}>
        <Link to="/store">Store</Link> / {p.category} / {p.brand}
      </div>
      <div className="sz-pdp">
        <div className="sz-card-img" style={{ borderRadius: 24, overflow: 'hidden' }}>
          <img src={productImage(p)} alt={p.title} data-testid="pdp-image" />
        </div>
        <div>
          <div className="sz-meta">{p.category?.toUpperCase()}</div>
          <h1 style={{ fontSize: 40, fontWeight: 600, letterSpacing: '-0.02em', margin: '8px 0' }} data-testid="pdp-title">{p.title}</h1>
          <p className="sz-meta">★ {p.rating} · {p.brand} · {p.sku}</p>
          <p className="sz-price" style={{ fontSize: 28, margin: '16px 0' }} data-testid="pdp-price">{formatPrice(p.price_inr)}</p>
          <p style={{ color: 'var(--muted)', lineHeight: 1.6 }} data-testid="pdp-desc">{p.description}</p>
          <ul data-testid="pdp-attrs" style={{ color: 'var(--muted)', fontSize: 14 }}>
            {Object.entries(p.attributes || {}).map(([k, v]) => <li key={k}>{k}: {String(v)}</li>)}
          </ul>
          <label className="sz-meta">Qty</label>
          <select className="sz-input" style={{ width: 100, margin: '8px 0 16px' }} data-testid="pdp-qty" value={qty} onChange={(e) => setQty(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
            <button type="button" className="sz-btn sz-btn-blue" data-testid="pdp-add" onClick={() => addToCart(p.id, p.title, p.price_inr, qty)}>Add to Bag</button>
            <button
              type="button"
              className="sz-btn sz-btn-dark"
              data-testid="pdp-buy"
              onClick={() => {
                addToCart(p.id, p.title, p.price_inr, qty)
                nav('/delivery')
              }}
            >
              Buy
            </button>
            <button type="button" className="sz-btn sz-btn-ghost" data-testid="pdp-save" onClick={() => toggleWishlist(p.id, p.title, p.price_inr)}>Save</button>
            <a className="sz-btn sz-btn-ghost" href={`/shop/product/${p.id}`} target="_blank" rel="noreferrer" data-testid="pdp-new-tab">Open in new tab</a>
          </div>
        </div>
      </div>
      {/* Separate product-page customer chatbot (isolated conversation per product) */}
      <ChatWidget persona="product" productContext={p} />
    </div>
  )
}
