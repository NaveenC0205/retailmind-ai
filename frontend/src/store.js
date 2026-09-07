/** Cart + wishlist local store (automation-friendly) */
const CART_KEY = 'cart';
const WISH_KEY = 'wishlist';

function read(key) {
  try { return JSON.parse(localStorage.getItem(key) || '[]'); } catch { return []; }
}
function write(key, val) {
  localStorage.setItem(key, JSON.stringify(val));
  window.dispatchEvent(new Event('shopzone-store'));
}

export function getCart() { return read(CART_KEY); }
export function getWishlist() { return read(WISH_KEY); }

export function addToCart(id, title, price, qty = 1) {
  const cart = getCart();
  const row = cart.find((x) => x.id === id);
  if (row) row.qty += qty;
  else cart.push({ id, title, price, qty });
  write(CART_KEY, cart);
}

export function setCartQty(id, qty) {
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
  URL.revokeObjectURL(url);
}

export function downloadOrdersCsv(orders) {
  const header = 'order_id,status,total_inr,placed_at,items\n';
  const rows = (orders || []).map((o) =>
    [o.id, o.status, o.total_inr, o.placed_at, (o.items || []).length].join(',')
  );
  downloadText('order-history.csv', header + rows.join('\n'), 'text/csv');
}
