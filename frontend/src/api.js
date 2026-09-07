/** API + auth helpers for ShopZone React storefront */
const API = '';

export function getToken() {
  return localStorage.getItem('auth_token');
}

const CHAT_KEY_PREFIX = 'sz-chat-';
const GUEST_SID_KEY = 'sz-guest-sid';

export function authHeaders(json = true) {
  const h = {};
  const t = getToken();
  // Guests stay guests. Never impersonate CU-1001 — that mixed every visitor
  // into one customer's orders and chat thread.
  h.Authorization = t ? `Bearer ${t}` : 'Bearer guest';
  if (json) h['Content-Type'] = 'application/json';
  return h;
}

export function chatActorId() {
  if (typeof window === 'undefined') return 'guest';
  if (isLoggedIn()) {
    return `cust-${localStorage.getItem('customer_id') || 'anon'}`;
  }
  let sid = sessionStorage.getItem(GUEST_SID_KEY);
  if (!sid) {
    sid = `g-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    sessionStorage.setItem(GUEST_SID_KEY, sid);
  }
  return sid;
}

export function clearChatSessions() {
  if (typeof window === 'undefined') return;
  const drop = [];
  for (let i = 0; i < sessionStorage.length; i += 1) {
    const k = sessionStorage.key(i);
    if (k && (k.startsWith(CHAT_KEY_PREFIX) || k === GUEST_SID_KEY)) drop.push(k);
  }
  drop.forEach((k) => sessionStorage.removeItem(k));
  const localDrop = [];
  for (let i = 0; i < localStorage.length; i += 1) {
    const k = localStorage.key(i);
    if (k && k.startsWith(CHAT_KEY_PREFIX)) localDrop.push(k);
  }
  localDrop.forEach((k) => localStorage.removeItem(k));
}

export function isLoggedIn() {
  return !!getToken();
}

export function isAdmin() {
  return localStorage.getItem('is_admin') === 'true';
}

export function saveSession(data) {
  clearChatSessions();
  localStorage.setItem('auth_token', data.access_token);
  localStorage.setItem('customer_id', data.customer_id);
  localStorage.setItem('customer_name', data.name);
  localStorage.setItem('customer_email', data.email);
  localStorage.setItem('is_admin', String(data.is_admin));
  window.dispatchEvent(new Event('shopzone-store'));
}

export function logout() {
  clearChatSessions();
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
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 45000);
  const headers = authHeaders();
  try {
    const res = await fetch(`${API}/api/chat/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    });
    if (!res.ok || !res.body) {
      const fallback = await fetch(`${API}/api/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
        signal: ctrl.signal,
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
          if (eventName === 'error') {
            throw new Error(data.message || data.detail || 'Chat failed');
          }
          if (eventName === 'final') finalPayload = data;
        } catch (err) {
          if (err instanceof SyntaxError) continue;
          throw err;
        }
      }
    }
    if (!finalPayload) {
      throw new Error('Chat ended without an answer. Please try again.');
    }
    return finalPayload;
  } catch (e) {
    if (e?.name === 'AbortError') {
      throw new Error('That took too long. Try a shorter question, or sign in and retry.');
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
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
