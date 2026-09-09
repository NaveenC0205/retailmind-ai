/** Cart + wishlist local store (automation-friendly) */
const CART_KEY = 'cart';
const WISH_KEY = 'wishlist';

function read(key) {
  try {
    const rows = JSON.parse(localStorage.getItem(key) || '[]');
    if (!Array.isArray(rows)) return [];
    return rows.filter((row) => row && typeof row.id === 'string' && typeof row.title === 'string' && Number.isFinite(row.price) && row.price >= 0 && (key !== CART_KEY || (Number.isInteger(row.qty) && row.qty > 0 && row.qty <= 100)));
  } catch { return []; }
}
function write(key, val) {
  localStorage.setItem(key, JSON.stringify(val));
  window.dispatchEvent(new Event('shopzone-store'));
}

export function getCart() { return read(CART_KEY); }
export function getWishlist() { return read(WISH_KEY); }

export function addToCart(id, title, price, qty = 1) {
  qty = Number(qty);
  if (!Number.isInteger(qty) || qty < 1 || qty > 100 || !Number.isFinite(price) || price < 0) return;
  const cart = getCart();
  const row = cart.find((x) => x.id === id);
  if (row) row.qty = Math.min(100, row.qty + qty);
  else cart.push({ id, title, price, qty });
  write(CART_KEY, cart);
  window.dispatchEvent(new CustomEvent('shopzone-notice', { detail: `${title} added to your bag` }));
}

export function setCartQty(id, qty) {
  qty = Number(qty);
  if (!Number.isInteger(qty) || qty < 0 || qty > 100) return;
  write(CART_KEY, getCart().map((x) => (x.id === id ? { ...x, qty: Number(qty) } : x)).filter((x) => x.qty > 0));
}

export function removeFromCart(id) {
  write(CART_KEY, getCart().filter((x) => x.id !== id));
}

export function clearCart() { write(CART_KEY, []); }

export function cartCount() {
  return getCart().reduce((s, i) => s + i.qty, 0);
}

export function toggleWishlist(id, title, price) {
  const list = getWishlist();
  const i = list.findIndex((x) => x.id === id);
  if (i >= 0) list.splice(i, 1);
  else list.push({ id, title, price });
  write(WISH_KEY, list);
  window.dispatchEvent(new CustomEvent('shopzone-notice', { detail: i < 0 ? 'Item saved to your wishlist' : 'Item removed from your wishlist' }));
  return i < 0;
}

export function wishCount() { return getWishlist().length; }

export function downloadText(filename, text, mime = 'text/plain') {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.setAttribute('data-testid', 'download-trigger');
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadOrdersCsv(orders) {
  const header = 'order_id,status,total_inr,placed_at,items\n';
  const rows = (orders || []).map((o) =>
    [o.id, o.status, o.total_inr, o.placed_at, (o.items || []).length].join(',')
  );
  downloadText('order-history.csv', header + rows.join('\n'), 'text/csv');
}
