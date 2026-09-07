import { Link } from 'react-router-dom'
import { askAgent, formatPrice, productImage } from '../api'
import { addToCart, toggleWishlist } from '../store'

export default function ProductCard({ p }) {
  return (
    <article className="sz-card" data-testid={`product-card-${p.id}`} data-product-id={p.id}>
      <Link to={`/product/${p.id}`} data-testid={`product-link-${p.id}`}>
        <div className="sz-card-img">
          <img src={productImage(p)} alt={p.title} loading="lazy" data-testid={`product-img-${p.id}`} />
        </div>
      </Link>
      <div className="sz-card-body">
        <div className="sz-meta">{p.category} · {p.brand}</div>
        <Link to={`/product/${p.id}`} className="sz-card-title">{p.title}</Link>
        <div className="sz-meta">★ {p.rating}</div>
        <div className="sz-price">{formatPrice(p.price_inr)}</div>
        <div className="sz-card-actions" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
          <button type="button" className="sz-btn sz-btn-blue" data-testid={`add-cart-${p.id}`} onClick={() => addToCart(p.id, p.title, p.price_inr)}>Add</button>
          <button type="button" className="sz-btn sz-btn-ghost" data-testid={`save-${p.id}`} onClick={() => toggleWishlist(p.id, p.title, p.price_inr)}>Save</button>
          <button type="button" className="sz-btn sz-btn-ghost" data-testid={`ask-product-${p.id}`} onClick={() => askAgent(`What's the price, stock and warranty for ${p.title}?`)}>Ask</button>
        </div>
      </div>
    </article>
  )
}
