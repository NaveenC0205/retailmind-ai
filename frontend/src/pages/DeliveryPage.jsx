import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { askAgent, formatPrice, isLoggedIn, placeOrder, productImage } from '../api'
import { clearCart, getCart } from '../store'
import Loading from '../components/Loading'

function addDays(d, n) {
  const x = new Date(d)
  x.setDate(x.getDate() + n)
  return `${x.getFullYear()}-${String(x.getMonth()+1).padStart(2,'0')}-${String(x.getDate()).padStart(2,'0')}`
}

const COUPONS = {
  SAVE10: { pct: 10, label: '10% off' },
  SAVE50: { flat: 50, label: '₹50 off' },
  FREESHIP: { pct: 0, label: 'Free shipping promo' },
}

export default function DeliveryPage() {
  const submitLock = useRef(false)
  const pending = useRef(null)
  const [error, setError] = useState('')
  const [cart, setCart] = useState(getCart())
  const [addr, setAddr] = useState('')
  const [pay, setPay] = useState('upi')
  const [deliveryDate, setDeliveryDate] = useState(addDays(new Date(), 3))
  const [coupon, setCoupon] = useState('')
  const [couponFrom, setCouponFrom] = useState(addDays(new Date(), -1))
  const [couponTo, setCouponTo] = useState(addDays(new Date(), 30))
  const [couponMsg, setCouponMsg] = useState('')
  const [appliedCoupon, setAppliedCoupon] = useState('')
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()

  useEffect(() => {
    const fn = () => setCart(getCart())
    window.addEventListener('shopzone-store', fn)
    return () => window.removeEventListener('shopzone-store', fn)
  }, [])

  const subtotal = cart.reduce((s, i) => s + i.price * i.qty, 0)
  const discount = Math.min(subtotal, appliedCoupon === 'SAVE10' ? Math.round(subtotal / 10) : appliedCoupon === 'SAVE50' ? 50 : 0)
  const total = Math.max(0, subtotal - discount)
  const minDate = addDays(new Date(), 1)
  const maxDate = addDays(new Date(), 21)

  const deliverySlots = useMemo(() => {
    const slots = []
    for (let i = 1; i <= 14; i++) slots.push(addDays(new Date(), i))
    return slots
  }, [])

  function applyCoupon() {
    const code = coupon.trim().toUpperCase()
    const today = addDays(new Date(), 0)
    if (today < couponFrom || today > couponTo) {
      setAppliedCoupon('')
      setCouponMsg('Coupon date window invalid for today — adjust From/To dates')
      return
    }
    const c = COUPONS[code]
    if (!c) {
      setAppliedCoupon('')
      setCouponMsg('Unknown coupon. Try SAVE10, SAVE50, FREESHIP')
      return
    }
    const d = c.flat ? c.flat : Math.round(subtotal * (c.pct / 100))
    setAppliedCoupon(code)
    setCouponMsg(`Applied ${code}: ${c.label} (−${formatPrice(d)})`)
  }

  async function place() {
    if (submitLock.current) return
    setError('')
    if (!isLoggedIn()) {
      nav('/login?redirect=/shop/delivery')
      return
    }
    if (!addr.trim() || deliveryDate < minDate || deliveryDate > maxDate) {
      setError('Enter your shipping address and choose a delivery date within the available range.')
      return
    }
    submitLock.current = true
    setBusy(true)
    try {
      const items = cart.map((i) => ({ product_id: i.id, qty: i.qty }))
      const fingerprint = JSON.stringify({ items, addr, pay, appliedCoupon, deliveryDate })
      if (pending.current?.fingerprint !== fingerprint) pending.current = { fingerprint, id: crypto.randomUUID() }
      const data = await placeOrder(items, addr.trim(), pay, { coupon_code: appliedCoupon, delivery_date: deliveryDate, request_id: pending.current.id })
      clearCart()
      nav(`/orders?placed=${data.order_id}`)
    } catch (e) {
      setError(e.message)
    } finally {
      submitLock.current = false
      setBusy(false)
    }
  }

  if (!cart.length) {
    return (
      <div className="sz-wrap" data-testid="delivery-empty">
        <h1>No items to deliver</h1>
        <Link to="/store" className="sz-btn sz-btn-blue">Continue shopping</Link>
      </div>
    )
  }

  return (
    <div className="sz-wrap" data-testid="delivery-page">
      <h1 style={{ fontSize: 40, fontWeight: 600 }}>Delivery & checkout</h1>
      <p className="sz-meta">Review your items, delivery details, and payment method before placing your order.</p>

      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.8fr', gap: 20 }} className="sz-delivery-grid">
        <div style={{ background: '#fff', borderRadius: 18, padding: 20 }}>
          <h2 style={{ marginTop: 0 }}>Shipping address</h2>
          <textarea className="sz-input" data-testid="delivery-address" aria-label="Shipping address" rows={3} value={addr} onChange={(e) => setAddr(e.target.value)} />

          <h2>Preferred delivery date</h2>
          <input
            className="sz-input"
            type="date"
            data-testid="delivery-date" aria-label="Preferred delivery date"
            min={minDate}
            max={maxDate}
            value={deliveryDate}
            onChange={(e) => setDeliveryDate(e.target.value)}
          />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }} data-testid="delivery-slots">
            {deliverySlots.slice(0, 7).map((d) => (
              <button
                key={d}
                type="button"
                className={`sz-btn ${deliveryDate === d ? 'sz-btn-blue' : 'sz-btn-ghost'}`}
                data-testid={`delivery-slot-${d}`}
                onClick={() => setDeliveryDate(d)}
              >
                {d}
              </button>
            ))}
          </div>

          <h2>Coupon</h2><p className="sz-meta">Use SAVE10 or SAVE50. Discount eligibility is checked at checkout.</p>
          <details><summary className="sz-meta">Coupon date-window preview (testing only)</summary><p className="sz-meta">These dates control the local preview, not the store’s coupon rules.</p><div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div>
              <label className="sz-meta">Valid from</label>
              <input className="sz-input" type="date" data-testid="coupon-from" value={couponFrom} onChange={(e) => setCouponFrom(e.target.value)} />
            </div>
            <div>
              <label className="sz-meta">Valid to</label>
              <input className="sz-input" type="date" data-testid="coupon-to" value={couponTo} onChange={(e) => setCouponTo(e.target.value)} />
            </div>
          </div>
          </details><div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <input className="sz-input" data-testid="coupon-code" placeholder="SAVE10 / SAVE50 / FREESHIP" value={coupon} onChange={(e) => { setCoupon(e.target.value); setAppliedCoupon(''); setCouponMsg('') }} />
            <button type="button" className="sz-btn sz-btn-dark" data-testid="coupon-apply" onClick={applyCoupon}>Apply</button>
          </div>
          {couponMsg && <p className="sz-meta" data-testid="coupon-msg">{couponMsg}</p>}

          <h2>Payment</h2>
          <select className="sz-input" data-testid="delivery-payment" aria-label="Payment method" value={pay} onChange={(e) => setPay(e.target.value)}>
            <option value="upi">UPI</option>
            <option value="card">Card</option>
            <option value="cod">Cash on delivery</option>
          </select>
        </div>

        <div style={{ background: '#fff', borderRadius: 18, padding: 20 }} data-testid="delivery-summary">
          <h2 style={{ marginTop: 0 }}>Order summary</h2>
          {cart.map((i) => (
            <div key={i.id} style={{ display: 'flex', gap: 10, marginBottom: 10, alignItems: 'center' }}>
              <img src={productImage({ id: i.id })} alt="" width={48} height={48} style={{ borderRadius: 8, objectFit: 'cover' }} />
              <div style={{ flex: 1 }}>
                <div className="sz-meta">{i.title} × {i.qty}</div>
              </div>
              <strong>{formatPrice(i.price * i.qty)}</strong>
            </div>
          ))}
          <div className="sz-meta">Subtotal {formatPrice(subtotal)}</div>
          <div className="sz-meta">Discount −{formatPrice(discount)}</div>
          <div className="sz-meta">Deliver on <strong data-testid="summary-delivery-date">{deliveryDate}</strong></div>
          <div style={{ fontSize: 22, fontWeight: 600, margin: '12px 0' }} data-testid="delivery-total">Total {formatPrice(total)}</div>
          {error && <p className="sz-page-error" role="alert">{error}</p>}
          {busy ? <Loading label="Placing order…" /> : (
            <button type="button" className="sz-btn sz-btn-blue sz-btn-full" data-testid="delivery-place" onClick={place}>
              Place order
            </button>
          )}
          <button
            type="button"
            className="sz-btn sz-btn-ghost sz-btn-full"
            style={{ marginTop: 8 }}
            data-testid="delivery-ask-agent"
            onClick={() => askAgent(`Buy ${cart[0]?.title || 'this'} with ${pay === 'cod' ? 'cash on delivery' : pay.toUpperCase()}`)}
          >
            Ask agent to buy the first item
          </button>
          <Link to="/cart" className="sz-btn sz-btn-ghost sz-btn-full" style={{ marginTop: 8 }}>Back to cart</Link>
        </div>
      </div>
    </div>
  )
}
