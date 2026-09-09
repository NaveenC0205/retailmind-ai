import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { askAgent, formatPrice, productImage } from '../api'
import { addToCart, getWishlist, toggleWishlist } from '../store'

export default function WishlistPage() {
  const [items, setItems] = useState(getWishlist())
  useEffect(() => {
    const fn = () => setItems(getWishlist())
    window.addEventListener('shopzone-store', fn)
    return () => window.removeEventListener('shopzone-store', fn)
  }, [])

  return (
    <div className="sz-wrap" data-testid="wishlist-page">
      <h1 style={{ fontSize: 40, fontWeight: 600 }}>Saved</h1>
      {!items.length && <p data-testid="wishlist-empty">Nothing saved. <Link to="/store">Browse store</Link></p>}
      <div className="sz-grid" style={{ marginTop: 24 }}>
        {items.map((p) => (
          <article data-depth-card key={p.id} className="sz-card" data-testid={`wish-${p.id}`}>
            <Link to={`/product/${p.id}`}><div className="sz-card-img"><img src={productImage(p)} alt="" /></div></Link>
            <div className="sz-card-body">
              <div className="sz-card-title">{p.title}</div>
              <div className="sz-price">{formatPrice(p.price)}</div>
              <div className="sz-card-actions">
                <button type="button" className="sz-btn sz-btn-blue" onClick={() => { addToCart(p.id, p.title, p.price); toggleWishlist(p.id) }}>Move to bag</button>
                <button type="button" className="sz-btn sz-btn-ghost" onClick={() => askAgent(`What's the price, stock and warranty for ${p.title}?`)}>Ask agent</button>
                <button type="button" className="sz-btn sz-btn-ghost" onClick={() => toggleWishlist(p.id)}>Remove</button>
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
