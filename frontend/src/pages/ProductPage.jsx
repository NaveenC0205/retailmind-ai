import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { fetchProduct, formatPrice } from '../api'
import { addToCart, toggleWishlist } from '../store'
import ChatWidget from '../components/ChatWidget'
import ProductViewer from '../components/ProductViewer'
import Loading from '../components/Loading'

export default function ProductPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const [p, setP] = useState(null)
  const [qty, setQty] = useState(1)
  const [err, setErr] = useState('')

  useEffect(() => {
    let active = true
    setP(null); setErr(''); setQty(1)
    fetchProduct(id).then((product) => { if (active) setP(product) }).catch((e) => { if (active) setErr(e.message) })
    return () => { active = false }
  }, [id])

  if (err) return <div className="sz-wrap" data-testid="product-error">{err}</div>
  if (!p) return <Loading label="Loading product…" testId="product-loading" />

  return (
    <div className="sz-wrap" data-testid="product-page">
      <div className="sz-meta" style={{ marginBottom: 12 }}>
        <Link to="/store">Store</Link> / {p.category} / {p.brand}
      </div>
      <nav className="sz-product-sections" aria-label="Product sections"><a href="#product-overview">Overview</a><a href="#product-specifications">Specifications</a><a href="#product-shopping-help">Shopping help</a></nav>
      <div className="sz-pdp" id="product-overview">
        <ProductViewer key={p.id} product={p} />
        <div>
          <div className="sz-meta">{p.category?.toUpperCase()}</div>
          <h1 style={{ fontSize: 40, fontWeight: 600, letterSpacing: '-0.02em', margin: '8px 0' }} data-testid="pdp-title">{p.title}</h1>
          <p className="sz-meta">★ {p.rating} · {p.brand} · {p.sku}</p>
          <p className="sz-price" style={{ fontSize: 28, margin: '16px 0' }} data-testid="pdp-price">{formatPrice(p.price_inr)}</p>
          <p style={{ color: 'var(--muted)', lineHeight: 1.6 }} data-testid="pdp-desc">{p.description}</p>
          <label className="sz-meta" htmlFor="product-quantity">Quantity</label>
          <select className="sz-input" style={{ width: 100, margin: '8px 0 16px' }} data-testid="pdp-qty" id="product-quantity" value={qty} onChange={(e) => setQty(Number(e.target.value))}>
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
            <button type="button" className="sz-btn sz-btn-ghost" data-testid="pdp-ask-buy" onClick={() => window.dispatchEvent(new CustomEvent('shopzone-ask-agent', { detail: { text: `Buy this ${p.title} with UPI`, persona: 'product' } }))}>Ask agent to buy</button>
            <button type="button" className="sz-btn sz-btn-ghost" data-testid="pdp-ask-policy" onClick={() => window.dispatchEvent(new CustomEvent('shopzone-ask-agent', { detail: { text: 'What is the warranty and return window for this item?', persona: 'product' } }))}>Ask policy</button>
            <a className="sz-btn sz-btn-ghost" href={`/shop/product/${p.id}`} target="_blank" rel="noreferrer" data-testid="pdp-new-tab">Open in new tab</a>
          </div>
        </div>
      </div>
      <section className="sz-product-detail-section" id="product-specifications">
        <span className="sz-eyebrow">THE DETAILS</span><h2>Get to know {p.title}</h2>
        <dl className="sz-product-specs" data-testid="pdp-attrs">
          <div><dt>Brand</dt><dd>{p.brand || 'Not provided'}</dd></div><div><dt>Category</dt><dd>{p.category}</dd></div><div><dt>Product code</dt><dd>{p.sku || p.id}</dd></div>
          {Object.entries(p.attributes || {}).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{typeof value === 'boolean' ? value ? 'Yes' : 'No' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}
        </dl>
      </section>
      <section className="sz-product-detail-section" id="product-shopping-help">
        <span className="sz-eyebrow">BEFORE YOU CHOOSE</span><h2>A little help with the details.</h2>
        <div className="sz-product-help-grid"><div><h3>Delivery & payment</h3><p>Add your selected quantity to the bag to review your address, preferred delivery date, and payment options at checkout.</p><Link to="/cart" className="sz-btn sz-btn-ghost">Review your bag →</Link></div><div><h3>Warranty & returns</h3><p>Ask the assistant to check the applicable store policy and sources for this product.</p><button type="button" className="sz-btn sz-btn-blue" onClick={() => window.dispatchEvent(new CustomEvent('shopzone-ask-agent', { detail: { text: `Check warranty and return policy for ${p.title}, with sources.`, persona: 'product' } }))}>Ask about this product ✦</button></div></div>
      </section>
      {/* Separate product-page customer chatbot (isolated conversation per product) */}
      <ChatWidget persona="product" productContext={p} />
    </div>
  )
}
