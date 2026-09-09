import { useEffect, useState } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { isAdmin, isLoggedIn, logout } from '../api'
import { cartCount, wishCount } from '../store'
import SearchBox from './SearchBox'

export default function Nav() {
  const [, setTick] = useState(0)
  const nav = useNavigate()
  useEffect(() => {
    const sync = () => setTick((n) => n + 1)
    window.addEventListener('shopzone-store', sync)
    window.addEventListener('storage', sync)
    return () => { window.removeEventListener('shopzone-store', sync); window.removeEventListener('storage', sync) }
  }, [])
  return <header className="sz-nav" data-testid="nav-bar">
    <a className="sz-skip" href="#main-content">Skip to content</a>
    <div className="sz-nav-inner">
      <Link to="/" className="sz-logo" data-testid="nav-logo"><span className="sz-logo-mark" aria-hidden="true">S</span>ShopZone<span className="sz-logo-dot">.</span></Link>
      <SearchBox />
      <nav className="sz-nav-actions" aria-label="Your account">
        <Link to="/orders" data-testid="nav-orders" className="hide-sm">Orders</Link>
        <Link to="/wishlist" data-testid="nav-wishlist">Saved <span className="sz-badge">{wishCount()}</span></Link>
        <Link to="/cart" data-testid="nav-cart">Bag <span className="sz-badge">{cartCount()}</span></Link>
        {isLoggedIn() ? <button type="button" className="link" data-testid="nav-logout" onClick={() => { logout(); nav('/') }}>Sign out</button> : <Link to="/login" data-testid="nav-login">Sign in ↗</Link>}
      </nav>
    </div>
    <nav className="sz-category-nav" aria-label="Main navigation">
      <NavLink to="/" end>Discover</NavLink><NavLink to="/store">All products</NavLink><Link to="/c/phones">Phones</Link><Link to="/c/laptops">Laptops</Link><Link to="/c/audio">Audio</Link><Link to="/c/accessories">Accessories</Link>
      <NavLink to="/lab" data-testid="nav-lab" className="sz-lab-nav">✦ AI Test Lab</NavLink>
      {isAdmin() && <NavLink to="/admin" data-testid="nav-admin">Seller workspace</NavLink>}
    </nav>
  </header>
}
export function MobileTabs() {
  return <nav className="sz-mobile-tabs" aria-label="Mobile navigation" data-testid="mobile-tabs"><NavLink to="/" end data-testid="tab-home">Home</NavLink><NavLink to="/store" data-testid="tab-store">Store</NavLink><NavLink to="/cart" data-testid="tab-cart">Bag</NavLink><NavLink to="/lab" data-testid="tab-lab">AI Lab</NavLink><NavLink to="/orders" data-testid="tab-orders">Orders</NavLink></nav>
}
export function Footer() {
  return <footer className="sz-footer" data-testid="footer"><div className="sz-footer-intro"><Link to="/" className="sz-logo">ShopZone.</Link><p>Find your next favorite.<br />Explore what AI can do.</p></div><div className="sz-footer-grid"><div><h4>Explore</h4><Link to="/store">All products</Link><Link to="/store?cat=phones">Phones</Link><Link to="/store?cat=laptops">Laptops</Link></div><div><h4>Your shopping</h4><Link to="/orders">Order history</Link><Link to="/cart">Shopping bag</Link><Link to="/wishlist">Saved items</Link><Link to="/delivery">Checkout</Link></div><div><h4>Workspaces</h4><Link to="/lab">AI testing workshop</Link><Link to="/admin">Seller dashboard</Link><Link to="/login">Sign in</Link></div><div><h4>Developer tools</h4><a href="/docs" target="_blank" rel="noreferrer">API documentation ↗</a><a href="/api/products?limit=5" target="_blank" rel="noreferrer">Product catalogue API ↗</a></div></div><div className="sz-footer-bottom">© {new Date().getFullYear()} ShopZone <span>Shopping · AI assistance · Hands-on learning</span></div></footer>
}
