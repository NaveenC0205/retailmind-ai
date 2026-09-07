import { useEffect, useState } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { isAdmin, isLoggedIn, logout } from '../api'
import { cartCount, wishCount } from '../store'
import SearchBox from './SearchBox'

export default function Nav() {
  const [, setTick] = useState(0)
  const nav = useNavigate()

  useEffect(() => {
    const fn = () => setTick((t) => t + 1)
    window.addEventListener('shopzone-store', fn)
    return () => window.removeEventListener('shopzone-store', fn)
  }, [])

  return (
    <header className="sz-nav" data-testid="nav-bar">
      <div className="sz-nav-inner">
        <Link to="/" className="sz-logo" data-testid="nav-logo">ShopZone</Link>
        <SearchBox />
        <Link to="/orders" data-testid="nav-orders" className="hide-sm">Orders</Link>
        <Link to="/lab" data-testid="nav-lab" className="hide-sm">Lab</Link>
        <span className="sz-nav-spacer" />
        <Link to="/wishlist" data-testid="nav-wishlist">Saved<span className="sz-badge">{wishCount()}</span></Link>
        <Link to="/cart" data-testid="nav-cart">Cart<span className="sz-badge">{cartCount()}</span></Link>
        {isAdmin() && <Link to="/admin" data-testid="nav-admin">Seller</Link>}
        {isLoggedIn() ? (
          <button type="button" className="link" data-testid="nav-logout" onClick={() => { logout(); nav('/'); }}>
            Sign out
          </button>
        ) : (
          <Link to="/login" data-testid="nav-login">Sign in</Link>
        )}
      </div>
    </header>
  )
}

export function MobileTabs() {
  return (
    <nav className="sz-mobile-tabs" data-testid="mobile-tabs">
      <NavLink to="/" end data-testid="tab-home">Home</NavLink>
      <NavLink to="/store" data-testid="tab-store">Store</NavLink>
      <NavLink to="/cart" data-testid="tab-cart">Cart</NavLink>
      <NavLink to="/orders" data-testid="tab-orders">Orders</NavLink>
      <NavLink to="/admin" data-testid="tab-admin">Seller</NavLink>
    </nav>
  )
}

export function Footer() {
  return (
    <footer className="sz-footer" data-testid="footer">
      <div className="sz-footer-grid">
        <div>
          <h4>Shop</h4>
          <Link to="/store?cat=phones">Phones</Link>
          <Link to="/store?cat=laptops">Laptops</Link>
          <Link to="/delivery">Delivery checkout</Link>
        </div>
        <div>
          <h4>Orders</h4>
          <Link to="/orders">Order history</Link>
          <Link to="/cart">Cart</Link>
          <Link to="/wishlist">Saved</Link>
        </div>
        <div>
          <h4>Seller</h4>
          <Link to="/admin">Owner dashboard</Link>
          <Link to="/lab">Test lab</Link>
        </div>
        <div>
          <h4>API</h4>
          <a href="/api/products?limit=5" target="_blank" rel="noreferrer">Products JSON</a>
          <a href="/docs" target="_blank" rel="noreferrer">OpenAPI docs</a>
        </div>
      </div>
      <p style={{ textAlign: 'center', marginTop: 28 }}>© 2026 ShopZone</p>
    </footer>
  )
}
