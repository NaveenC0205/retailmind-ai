/** API + auth helpers for ShopZone React storefront */
const API = '';

export function getToken() {
  return localStorage.getItem('auth_token');
}

export function authHeaders(json = true) {
  const h = {};
  const t = getToken();
  h.Authorization = t ? `Bearer ${t}` : `Bearer customer:${localStorage.getItem('customer_id') || 'CU-1001'}`;
  if (json) h['Content-Type'] = 'application/json';
  return h;
}

export function isLoggedIn() {
  return !!getToken();
}

export function isAdmin() {
  return localStorage.getItem('is_admin') === 'true';
}

export function saveSession(data) {
  localStorage.setItem('auth_token', data.access_token);
  localStorage.setItem('customer_id', data.customer_id);
  localStorage.setItem('customer_name', data.name);
  localStorage.setItem('customer_email', data.email);
  localStorage.setItem('is_admin', String(data.is_admin));
  window.dispatchEvent(new Event('shopzone-store'));
}

export function logout() {
  ['auth_token', 'customer_id', 'customer_name', 'customer_email', 'is_admin'].forEach((k) => localStorage.removeItem(k));
  window.dispatchEvent(new Event('shopzone-store'));
}

export async function fetchProducts() {
  const res = await fetch(`${API}/api/products?limit=500`);
  const data = await res.json();
  return data.products || [];
}

export async function fetchProduct(id) {
  const res = await fetch(`${API}/api/products/${id}`);
  if (!res.ok) throw new Error('Product not found');
  return res.json();
}

export async function login(email, password) {
  const res = await fetch(`${API}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Login failed');
  return data;
}

export async function register(name, email, password) {
  const res = await fetch(`${API}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, email, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Register failed');
  return data;
}

export async function placeOrder(items, address, payment_method) {
  const res = await fetch(`${API}/api/orders`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ items, address, payment_method }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Order failed');
  return data;
}

export async function fetchOrders() {
  const cid = localStorage.getItem('customer_id');
  const res = await fetch(`${API}/api/customers/${cid}/orders`, { headers: authHeaders(false) });
  const data = await res.json();
  return data.orders || [];
}

export async function fetchAgentStatus() {
  try {
    const res = await fetch(`${API}/health`);
    return await res.json();
  } catch {
    return { llm_live: false, llm_backend: 'mock', llm_model: 'mock-1' };
  }
}

export async function chatStream(message, conversationId, onEvent, opts = {}) {
  const payload = {
    message,
    mode: 'multi_agent',
    conversation_id: conversationId || undefined,
    learn: true,
    persona: opts.persona || 'customer',
    product_id: opts.productId || undefined,
  };
  const res = await fetch(`${API}/api/chat/stream`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) {
    const fallback = await fetch(`${API}/api/chat`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await fallback.json();
    if (!fallback.ok) throw new Error(data.detail || 'Chat failed');
    onEvent?.({ type: 'final', data });
    return data;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  let eventName = 'message';
  let finalPayload = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop() || '';
    for (const chunk of parts) {
      let dataLine = '';
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim();
        if (line.startsWith('data:')) dataLine += line.slice(5).trim();
      }
      if (!dataLine) continue;
      try {
        const data = JSON.parse(dataLine);
        onEvent?.({ type: eventName, data });
        if (eventName === 'final') finalPayload = data;
      } catch { /* ignore */ }
    }
  }
  return finalPayload;
}

export function formatPrice(n) {
  return `₹${Number(n).toLocaleString('en-IN')}`;
}

export function productImage(p) {
  const MAP = {
    laptops: 'https://images.unsplash.com/photo-1496181133206-80ce9b88a853?auto=format&fit=crop&w=1200&q=80',
    phones: 'https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=1200&q=80',
    audio: 'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=1200&q=80',
    monitors: 'https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?auto=format&fit=crop&w=1200&q=80',
    accessories: 'https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=1200&q=80',
  };
  if (p?.image_url) return p.image_url;
  const cat = (p?.category || '').toLowerCase();
  return MAP[cat] || MAP.laptops;
}
