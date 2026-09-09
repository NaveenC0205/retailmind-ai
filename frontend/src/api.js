/** API + auth helpers for ShopZone React storefront */
const API = '';

export function getToken() {
  return localStorage.getItem('auth_token');
}

const CHAT_KEY_PREFIX = 'sz-chat-';
const GUEST_SID_KEY = 'sz-guest-sid';
let memoryGuestId;

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
  let sid;
  try { sid = sessionStorage.getItem(GUEST_SID_KEY); } catch { /* Optional storage. */ }
  sid ||= memoryGuestId;
  if (!sid) {
    sid = `g-${crypto.randomUUID()}`;
    memoryGuestId = sid;
    try { sessionStorage.setItem(GUEST_SID_KEY, sid); } catch { /* Use this page session. */ }
  }
  return sid;
}

export function clearChatSessions() {
  memoryGuestId = undefined;
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
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load products');
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

export async function placeOrder(items, address, payment_method, extras = {}) {
  const res = await fetch(`${API}/api/orders`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ items, address, payment_method, ...extras }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Order failed');
  return data;
}

export async function fetchOrders() {
  const cid = localStorage.getItem('customer_id');
  const res = await fetch(`${API}/api/customers/${cid}/orders`, { headers: authHeaders(false) });
  const data = await res.json();
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load orders');
  return data.orders || [];
}

export async function fetchAgentStatus() {
  try {
    const res = await fetch(`${API}/health`);
    if (!res.ok) throw new Error('Health check failed');
    return await res.json();
  } catch {
    return { unavailable: true };
  }
}

export async function chatStream(message, conversationId, onEvent, opts = {}) {
  const payload = {
    message,
    mode: opts.mode || 'multi_agent',
    conversation_id: conversationId || undefined,
    learn: opts.learn !== false,
    persona: opts.persona || 'customer',
    product_id: opts.productId || undefined,
    prompt_version: opts.promptVersion || 'v1',
  };
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 45000);
  const cancel = () => ctrl.abort();
  opts.signal?.addEventListener('abort', cancel, { once: true });
  if (opts.signal?.aborted) ctrl.abort();
  let reader;
  const headers = authHeaders();
  if (!isLoggedIn()) headers['X-Chat-Session'] = chatActorId();
  try {
    const res = await fetch(`${API}/api/chat/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    });
    if ([404, 405, 501].includes(res.status)) {
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
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : `Chat failed (${res.status})`);
    }
    if (!res.body) throw new Error('Chat returned an empty response.');
    reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let finalPayload = null;
    function consume(chunk) {
      let eventName = 'message';
      const lines = [];
      for (const line of chunk.split(/\r?\n/)) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim();
        if (line.startsWith('data:')) lines.push(line.slice(5).replace(/^ /, ''));
      }
      if (!lines.length) return;
      let data;
      try { data = JSON.parse(lines.join('\n')); }
      catch { throw new Error('Chat returned an invalid response. Please try again.'); }
      if (eventName === 'error') throw new Error(data.message || data.detail || 'Chat failed');
      onEvent?.({ type: eventName, data });
      if (eventName === 'final') finalPayload = data;
    }
    while (!finalPayload) {
      const { done, value } = await reader.read();
      buf += done ? dec.decode() : dec.decode(value, { stream: true });
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(buf))) {
        consume(buf.slice(0, boundary.index));
        buf = buf.slice(boundary.index + boundary[0].length);
        if (finalPayload) break;
      }
      if (done) {
        if (!finalPayload && buf.trim()) consume(buf);
        break;
      }
    }
    if (!finalPayload) {
      throw new Error('Chat ended without an answer. Please try again.');
    }
    return finalPayload;
  } catch (e) {
    if (e?.name === 'AbortError' && !opts.signal?.aborted) {
      throw new Error('That took too long. Try a shorter question, or sign in and retry.');
    }
    throw e;
  } finally {
    clearTimeout(timer);
    opts.signal?.removeEventListener('abort', cancel);
    if (reader) {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
}

export function askAgent(text, extras = {}) {
  if (typeof window === 'undefined' || !text) return;
  window.dispatchEvent(new CustomEvent('shopzone-ask-agent', { detail: { text, ...extras } }));
}

export async function fetchJson(path, init = {}) {
  const res = await fetch(`${API}${path}`, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || `Request failed (${res.status})`);
  return data;
}

export async function fetchApprovals(status = 'pending') {
  return fetchJson(`/api/approvals?status=${encodeURIComponent(status)}`, { headers: authHeaders(false) });
}

export async function decideApproval(id, decision) {
  return fetchJson(`/api/approvals/${id}`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ decision }),
  });
}

export async function fetchRuns(limit = 20) {
  return fetchJson(`/api/runs?limit=${limit}`, { headers: authHeaders(false) });
}

export async function fetchRun(id) {
  return fetchJson(`/api/runs/${id}`, { headers: authHeaders(false) });
}

export async function fetchTools() {
  return fetchJson('/api/tools');
}

export async function fetchDatasets() {
  return fetchJson('/api/datasets');
}

export async function runEval(body) {
  return fetchJson('/api/eval/run', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
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
