import DeviceScene from '../components/DeviceScene'
import { useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { login, register, saveSession } from '../api'

export default function LoginPage() {
  const lock = useRef(false)
  const [busy, setBusy] = useState(false)
  const [role, setRole] = useState('customer')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [mode, setMode] = useState('login')
  const [err, setErr] = useState('')
  const [params] = useSearchParams()
  const nav = useNavigate()
  const redirect = params.get('redirect') || ''

  function switchRole(r) {
    setRole(r)
    setMode('login')
    setErr('')
    setPassword('')
  }

  async function onSubmit(e) {
    e.preventDefault()
    if (lock.current) return
    lock.current = true
    setBusy(true)
    setErr('')
    try {
      const data = mode === 'login' ? await login(email, password) : await register(name, email, password)
      if (mode === 'login') {
        if (role === 'owner' && !data.is_admin) throw new Error('Not a shop owner account')
        if (role === 'customer' && data.is_admin) throw new Error('Use Shop Owner tab for this account')
      }
      saveSession(data)
      if (redirect.startsWith('/shop/') && !redirect.includes('\\')) nav(redirect.slice(5))
      else nav(data.is_admin ? '/admin' : '/')
    } catch (ex) {
      setErr(ex.message)
    } finally { lock.current = false; setBusy(false) }
  }

  return (
    <div className="sz-auth-layout"><aside className="sz-auth-story"><DeviceScene compact /><span className="sz-eyebrow">WELCOME TO SHOPZONE</span><h2>Your next find<br />starts here.</h2><p>Save your favorites, follow your orders, and get help from your personal shopping assistant.</p></aside><div className="sz-wrap" style={{ maxWidth: 420 }} data-testid="login-page">
      <h1>{mode === 'login' ? 'Welcome back' : 'Create your account'}</h1><p className="sz-meta">{role === 'owner' ? 'Manage your store and agent operations.' : 'Sign in to continue your shopping journey.'}</p>
      <div style={{ display: 'flex', gap: 8, margin: '16px 0' }}>
        <button type="button" className={`sz-btn ${role==='customer'?'sz-btn-blue':'sz-btn-ghost'}`} data-testid="tab-customer" onClick={() => switchRole('customer')}>Customer</button>
        <button type="button" className={`sz-btn ${role==='owner'?'sz-btn-blue':'sz-btn-ghost'}`} data-testid="tab-owner" onClick={() => switchRole('owner')}>Shop Owner</button>
      </div>
      <form onSubmit={onSubmit} data-testid="login-form">
        {mode === 'register' && (
          <>
            <label className="sz-meta">Name</label>
            <input className="sz-input" data-testid="register-name" aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} required autoComplete="name" />
          </>
        )}
        <label className="sz-meta">Email</label>
        <input className="sz-input" data-testid="login-email" aria-label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" placeholder="Email" />
        <label className="sz-meta">Password</label>
        <input className="sz-input" data-testid="login-password" aria-label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" placeholder="Password" />
        {err && <p data-testid="login-error" style={{ color: '#b00020' }}>{err}</p>}
        <button type="submit" className="sz-btn sz-btn-blue sz-btn-full" style={{ marginTop: 12 }} data-testid="login-submit" disabled={busy}>
          {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
        </button>
      </form>
      {role === 'customer' && (
        <button type="button" className="sz-btn sz-btn-ghost sz-btn-full" style={{ marginTop: 10 }} data-testid="toggle-register" onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
          {mode === 'login' ? 'Create account' : 'Back to sign in'}
        </button>
      )}
    </div></div>
  )
}
