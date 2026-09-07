import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { login, register, saveSession } from '../api'

export default function LoginPage() {
  const [role, setRole] = useState('customer')
  const [email, setEmail] = useState('customer@shopzone.in')
  const [password, setPassword] = useState('customer123')
  const [name, setName] = useState('')
  const [mode, setMode] = useState('login')
  const [err, setErr] = useState('')
  const [params] = useSearchParams()
  const nav = useNavigate()
  const redirect = params.get('redirect') || ''

  function switchRole(r) {
    setRole(r)
    if (r === 'owner') {
      setEmail('owner@shopzone.in'); setPassword('owner123')
    } else {
      setEmail('customer@shopzone.in'); setPassword('customer123')
    }
  }

  async function onSubmit(e) {
    e.preventDefault()
    setErr('')
    try {
      const data = mode === 'login' ? await login(email, password) : await register(name, email, password)
      if (mode === 'login') {
        if (role === 'owner' && !data.is_admin) throw new Error('Not a shop owner account')
        if (role === 'customer' && data.is_admin) throw new Error('Use Shop Owner tab for this account')
      }
      saveSession(data)
      if (redirect) window.location.href = redirect
      else nav(data.is_admin ? '/admin' : '/')
    } catch (ex) {
      setErr(ex.message)
    }
  }

  return (
    <div className="sz-wrap" style={{ maxWidth: 420 }} data-testid="login-page">
      <h1 style={{ fontSize: 40, fontWeight: 600 }}>Sign in</h1>
      <div style={{ display: 'flex', gap: 8, margin: '16px 0' }}>
        <button type="button" className={`sz-btn ${role==='customer'?'sz-btn-blue':'sz-btn-ghost'}`} data-testid="tab-customer" onClick={() => switchRole('customer')}>Customer</button>
        <button type="button" className={`sz-btn ${role==='owner'?'sz-btn-blue':'sz-btn-ghost'}`} data-testid="tab-owner" onClick={() => switchRole('owner')}>Shop Owner</button>
      </div>
      <form onSubmit={onSubmit} data-testid="login-form">
        {mode === 'register' && (
          <>
            <label className="sz-meta">Name</label>
            <input className="sz-input" data-testid="register-name" value={name} onChange={(e) => setName(e.target.value)} required />
          </>
        )}
        <label className="sz-meta">Email</label>
        <input className="sz-input" data-testid="login-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <label className="sz-meta">Password</label>
        <input className="sz-input" data-testid="login-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        {err && <p data-testid="login-error" style={{ color: '#b00020' }}>{err}</p>}
        <button type="submit" className="sz-btn sz-btn-blue sz-btn-full" style={{ marginTop: 12 }} data-testid="login-submit">
          {mode === 'login' ? 'Sign in' : 'Create account'}
        </button>
      </form>
      {role === 'customer' && (
        <button type="button" className="sz-btn sz-btn-ghost sz-btn-full" style={{ marginTop: 10 }} data-testid="toggle-register" onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
          {mode === 'login' ? 'Create account' : 'Back to sign in'}
        </button>
      )}
      <p className="sz-meta" style={{ marginTop: 16 }} data-testid="demo-creds">Demo: customer@shopzone.in / customer123 · owner@shopzone.in / owner123</p>
    </div>
  )
}
