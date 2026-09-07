import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { formatPrice, productImage } from '../api'
import { getCart, removeFromCart, setCartQty } from '../store'

export default function CartPage() {
  const [cart, setCart] = useState(getCart())
  const nav = useNavigate()

  useEffect(() => {
    const fn = () => setCart(getCart())
    window.addEventListener('shopzone-store', fn)
    return () => window.removeEventListener('shopzone-store', fn)
  }, [])

  const total = cart.reduce((s, i) => s + i.price * i.qty, 0)

  if (!cart.length) {
    return (
      <div className="sz-wrap" data-testid="cart-empty">
        <h1>Your bag is empty</h1>
        <Link to="/store" className="sz-btn sz-btn-blue">Continue shopping</Link>
      </div>
    )
  }

  return (
    <div className="sz-wrap" data-testid="cart-page">
      <h1 style={{ fontSize: 40, fontWeight: 600 }}>Bag</h1>
      <div style={{ display: 'grid', gap: 16, marginTop: 24 }}>
        {cart.map((i) => (
          <div key={i.id} data-testid={`cart-item-${i.id}`} style={{ display: 'grid', gridTemplateColumns: '96px 1fr auto', gap: 16, background: '#fff', padding: 16, borderRadius: 18 }}>
            <img src={productImage({ id: i.id, title: i.title })} alt="" style={{ width: 96, height: 96, objectFit: 'cover', borderRadius: 12 }} />
            <div>
              <Link to={`/product/${i.id}`} className="sz-card-title">{i.title}</Link>
              <div className="sz-price">{formatPrice(i.price)}</div>
              <select data-testid={`cart-qty-${i.id}`} value={i.qty} onChange={(e) => setCartQty(i.id, e.target.value)} className="sz-input" style={{ width: 80, marginTop: 8 }}>
                {[1,2,3,4,5,6,7,8,9,10].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
              <button type="button" className="sz-btn sz-btn-ghost" style={{ marginLeft: 8 }} data-testid={`cart-remove-${i.id}`} onClick={() => removeFromCart(i.id)}>Remove</button>
            </div>
            <strong>{formatPrice(i.price * i.qty)}</strong>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 24, background: '#fff', padding: 20, borderRadius: 18, maxWidth: 420 }} data-testid="checkout-box">
        <div style={{ fontSize: 22, fontWeight: 600, marginBottom: 12 }}>Total {formatPrice(total)}</div>
        <button type="button" className="sz-btn sz-btn-blue sz-btn-full" data-testid="checkout-btn" onClick={() => nav('/delivery')}>
          Proceed to delivery
        </button>
      </div>
    </div>
  )
}
