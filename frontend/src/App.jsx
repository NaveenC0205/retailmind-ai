import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import DepthEffects from './components/DepthEffects'
import Nav, { Footer, MobileTabs } from './components/Nav'
import ChatWidget from './components/ChatWidget'
import StorePage from './pages/StorePage'
import ProductPage from './pages/ProductPage'
import CartPage from './pages/CartPage'
import DeliveryPage from './pages/DeliveryPage'
import OrdersPage from './pages/OrdersPage'
import OrderDetailPage from './pages/OrderDetailPage'
import WishlistPage from './pages/WishlistPage'
import LoginPage from './pages/LoginPage'
import AdminPage from './pages/AdminPage'
import LabPage from './pages/LabPage'
import './styles.css'
import './redesign.css'
import './depth.css'

/** Customer shopping chatbot — hidden on admin, login, and product pages (product has its own). */
function CustomerChat() {
  const { pathname } = useLocation()
  if (pathname.startsWith('/admin') || pathname.startsWith('/login') || pathname.startsWith('/product/') || pathname.startsWith('/lab')) {
    return null
  }
  return <ChatWidget persona="customer" />
}

function Notice() {
  const [message, setMessage] = useState('')
  useEffect(() => {
    let timer
    const show = (event) => { clearTimeout(timer); setMessage(event.detail); timer = setTimeout(() => setMessage(''), 3000) }
    window.addEventListener('shopzone-notice', show)
    return () => { clearTimeout(timer); window.removeEventListener('shopzone-notice', show) }
  }, [])
  return message ? <div className="sz-toast" role="status">{message}</div> : null
}

function PageFrame() {
  const location = useLocation()
  const [identity, setIdentity] = useState(() => localStorage.getItem('auth_token'))
  useEffect(() => {
    const sync = () => setIdentity(localStorage.getItem('auth_token'))
    window.addEventListener('shopzone-store', sync)
    window.addEventListener('storage', sync)
    return () => { window.removeEventListener('shopzone-store', sync); window.removeEventListener('storage', sync) }
  }, [])
  useEffect(() => { window.scrollTo(0, 0) }, [location.pathname])
  return <main id="main-content" key={`${location.pathname}:${location.search}:${identity}`}>
                <Routes>
          <Route path="/" element={<StorePage homeModules />} />
          <Route path="/store" element={<StorePage />} />
          <Route path="/c/:slug" element={<StorePage />} />
          <Route path="/product/:id" element={<ProductPage />} />
          <Route path="/cart" element={<CartPage />} />
          <Route path="/delivery" element={<DeliveryPage />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/orders/:id" element={<OrderDetailPage />} />
          <Route path="/wishlist" element={<WishlistPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/lab" element={<LabPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
}

export default function App() {
  return (
    <BrowserRouter basename="/shop">
      <Nav />
      <PageFrame />
      <Footer />
      <DepthEffects />
      <MobileTabs />
      <Notice />
      <CustomerChat />
    </BrowserRouter>
  )
}
