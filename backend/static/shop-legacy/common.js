/** Shared ShopZone electronics storefront utilities */
const Shop = {
  API: '',
  BRAND: 'ShopZone',
  ELEC_CATS: ['laptops', 'phones', 'audio', 'monitors', 'accessories'],

  getAuthToken() {
    return localStorage.getItem('auth_token');
  },

  getCustomerId() {
    return localStorage.getItem('customer_id') || '';
  },

  getAuthHeaders(json = false) {
    const headers = {};
    const token = this.getAuthToken();
    headers['Authorization'] = token ? `Bearer ${token}` : 'Bearer guest';
    if (json) headers['Content-Type'] = 'application/json';
    return headers;
  },

  isLoggedIn() {
    return !!this.getAuthToken();
  },

  isAdmin() {
    return localStorage.getItem('is_admin') === 'true';
  },

  getCart() {
    return JSON.parse(localStorage.getItem('cart') || '[]');
  },

  saveCart(cart) {
    localStorage.setItem('cart', JSON.stringify(cart));
    this.updateCartCount();
  },

  cartCount() {
    return this.getCart().reduce((sum, item) => sum + item.qty, 0);
  },

  updateCartCount() {
    const el = document.getElementById('cart-count');
    if (el) el.textContent = this.cartCount();
  },

  addToCart(id, title, price, qty = 1) {
    const cart = this.getCart();
    const existing = cart.find(item => item.id === id);
    if (existing) existing.qty += qty;
    else cart.push({ id, title, price, qty });
    this.saveCart(cart);
    this.toast('Added to cart');
  },

  getWishlist() {
    return JSON.parse(localStorage.getItem('wishlist') || '[]');
  },

  saveWishlist(list) {
    localStorage.setItem('wishlist', JSON.stringify(list));
    this.updateWishlistCount();
  },

  updateWishlistCount() {
    const el = document.getElementById('wish-count');
    if (el) el.textContent = this.getWishlist().length;
  },

  toggleWishlist(id, title, price) {
    const list = this.getWishlist();
    const i = list.findIndex(x => x.id === id);
    if (i >= 0) {
      list.splice(i, 1);
      this.saveWishlist(list);
      this.toast('Removed from Save for later');
      return false;
    }
    list.push({ id, title, price });
    this.saveWishlist(list);
    this.toast('Saved for later');
    return true;
  },

  isWishlisted(id) {
    return this.getWishlist().some(x => x.id === id);
  },

  logout() {
    ['auth_token', 'customer_id', 'customer_name', 'customer_email', 'is_admin'].forEach(k => localStorage.removeItem(k));
    window.location.href = '/shop/';
  },

  toast(message) {
    const t = document.createElement('div');
    t.className = 'cm-toast';
    t.textContent = message;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2500);
  },

  formatPrice(inr) {
    return `₹${Number(inr).toLocaleString('en-IN')}`;
  },

  stars(rating) {
    const full = Math.round(rating || 0);
    return `${'★'.repeat(full)}${'☆'.repeat(5 - full)}`;
  },

  productIcon(category) {
    const map = {
      laptops: 'laptop', phones: 'mobile-alt', audio: 'headphones',
      accessories: 'keyboard', monitors: 'desktop',
    };
    return map[(category || '').toLowerCase()] || 'box';
  },

  // Real Unsplash product photos (CDN) keyed by catalogue id
  PRODUCT_IMAGES: {
    'PR-L001': 'https://images.unsplash.com/photo-1496181133206-80ce9b88a853?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-L002': 'https://images.unsplash.com/photo-1525547719571-a2d4ac882e75?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-L003': 'https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-L004': 'https://images.unsplash.com/photo-1593642632823-8f785ba67e45?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-L005': 'https://images.unsplash.com/photo-1517336714731-489689fd1ca8?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-L006': 'https://images.unsplash.com/photo-1484788984921-03950022c9ef?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-L007': 'https://images.unsplash.com/photo-1611186871348-b1ce696e52c9?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-L008': 'https://images.unsplash.com/photo-1517336714731-489689fd1ca8?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-P001': 'https://images.unsplash.com/photo-1610945416475-ce6c0da83f66?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-P002': 'https://images.unsplash.com/photo-1598327105666-5b89351aff97?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-P003': 'https://images.unsplash.com/photo-1695048133142-1a20484d2569?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-P004': 'https://images.unsplash.com/photo-1610945265064-0e34e5519bbf?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-P005': 'https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-A001': 'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-A002': 'https://images.unsplash.com/photo-1590658268037-6bf12165a8df?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-A003': 'https://images.unsplash.com/photo-1600294037681-c80b4cb5b434?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-A004': 'https://images.unsplash.com/photo-1608043152269-423dbba4e7e1?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-M001': 'https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-M002': 'https://images.unsplash.com/photo-1585792187661-66877904d2b6?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-M003': 'https://images.unsplash.com/photo-1616763355548-1b57a3048744?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-X001': 'https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-X002': 'https://images.unsplash.com/photo-1527864550417-7fd91fc51a46?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-X003': 'https://images.unsplash.com/photo-1625948515291-69613efd103f?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-X004': 'https://images.unsplash.com/photo-1553062407-98eeb64c6a62?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J001': 'https://images.unsplash.com/photo-1535632066927-ab7c9ab60908?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J002': 'https://images.unsplash.com/photo-1611591437281-460bfbe1220a?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J003': 'https://images.unsplash.com/photo-1630019852942-f89202989a59?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J004': 'https://images.unsplash.com/photo-1515562141207-7a88fb7ce338?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J005': 'https://images.unsplash.com/photo-1605100804763-247f67b3557e?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J006': 'https://images.unsplash.com/photo-1603561591411-07134e71a2a9?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J007': 'https://images.unsplash.com/photo-1605100804763-247f67b3557e?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J008': 'https://images.unsplash.com/photo-1599643478518-a784e5dc4c8f?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J009': 'https://images.unsplash.com/photo-1611652022419-a9419f74343d?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J010': 'https://images.unsplash.com/photo-1602173574767-37ac01994b2a?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J011': 'https://images.unsplash.com/photo-1599643477877-530eb83abc8e?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J012': 'https://images.unsplash.com/photo-1611591437281-460bfbe1220a?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J013': 'https://images.unsplash.com/photo-1573408301185-9146fe634ad0?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J014': 'https://images.unsplash.com/photo-1611085583191-a3b181a88401?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J015': 'https://images.unsplash.com/photo-1601121141461-9d791f338d83?auto=format&fit=crop&w=800&h=800&q=80',
    'PR-J016': 'https://images.unsplash.com/photo-1605100804763-247f67b3557e?auto=format&fit=crop&w=800&h=800&q=80',
  },

  CATEGORY_IMAGES: {
    laptops: [
      'https://images.unsplash.com/photo-1496181133206-80ce9b88a853?auto=format&fit=crop&w=800&h=800&q=80',
      'https://images.unsplash.com/photo-1525547719571-a2d4ac882e75?auto=format&fit=crop&w=800&h=800&q=80',
      'https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?auto=format&fit=crop&w=800&h=800&q=80',
    ],
    phones: [
      'https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=800&h=800&q=80',
      'https://images.unsplash.com/photo-1598327105666-5b89351aff97?auto=format&fit=crop&w=800&h=800&q=80',
      'https://images.unsplash.com/photo-1610945416475-ce6c0da83f66?auto=format&fit=crop&w=800&h=800&q=80',
    ],
    audio: [
      'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=800&h=800&q=80',
      'https://images.unsplash.com/photo-1590658268037-6bf12165a8df?auto=format&fit=crop&w=800&h=800&q=80',
    ],
    monitors: [
      'https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?auto=format&fit=crop&w=800&h=800&q=80',
      'https://images.unsplash.com/photo-1585792187661-66877904d2b6?auto=format&fit=crop&w=800&h=800&q=80',
    ],
    accessories: [
      'https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=800&h=800&q=80',
      'https://images.unsplash.com/photo-1527864550417-7fd91fc51a46?auto=format&fit=crop&w=800&h=800&q=80',
    ],
  },

  _hashId(id) {
    let h = 0;
    for (let i = 0; i < String(id).length; i++) h = ((h << 5) - h) + String(id).charCodeAt(i);
    return Math.abs(h);
  },

  productImageUrl(product) {
    if (!product) return this.CATEGORY_IMAGES.laptops[0];
    if (product.image_url) return product.image_url;
    if (this.PRODUCT_IMAGES[product.id]) return this.PRODUCT_IMAGES[product.id];

    const cat = (product.category || '').toLowerCase();
    const pool = this.CATEGORY_IMAGES[cat];
    if (pool?.length) return pool[this._hashId(product.id) % pool.length];

    // Live keyword photo lookup (real photos by product name)
    const tags = [product.category, product.brand, (product.title || '').split(' ')[0]]
      .filter(Boolean)
      .map(t => String(t).toLowerCase().replace(/[^a-z0-9]+/g, ''))
      .filter(Boolean)
      .slice(0, 3)
      .join(',');
    const lock = this._hashId(product.id || product.title || 'x');
    return `https://loremflickr.com/800/800/${tags || 'electronics'}?lock=${lock}`;
  },

  toggleAccountMenu() {
    document.getElementById('account-dropdown')?.classList.toggle('show');
  },

  renderHeader(activePage = 'home') {
    const loggedIn = this.isLoggedIn();
    const name = localStorage.getItem('customer_name') || 'Guest';
    const first = loggedIn ? name.split(' ')[0] : 'Sign in';

    return `
    <div class="cm-promo">Free delivery on electronics · Easy returns · AI shopping assistant</div>
    <header class="cm-header">
      <div class="cm-header-inner">
        <a href="/shop/" class="cm-logo">shop<span>Zone</span></a>
        <form class="cm-search" onsubmit="return Shop.handleSearch(event)">
          <input type="text" id="search-input" placeholder="Search phones, laptops, headphones…">
          <button type="submit" aria-label="Search"><i class="fas fa-search"></i></button>
        </form>
        <div class="cm-header-actions">
          <div style="position:relative">
            <button type="button" onclick="Shop.toggleAccountMenu()">
              <i class="fas fa-user"></i> ${first}
            </button>
            <div id="account-dropdown" class="cm-dropdown">
              ${loggedIn ? `
                <div style="padding:10px 16px;font-size:12px;color:var(--cm-muted);border-bottom:1px solid var(--cm-line)">${localStorage.getItem('customer_email') || ''}</div>
                ${this.isAdmin()
                  ? `<a href="/shop/admin/">Seller Central</a><a href="/shop/admin/orders.html">Orders</a><a href="/shop/admin/products.html">Products</a>`
                  : `<a href="/shop/orders.html">Your Orders</a><a href="/shop/cart.html">Cart</a><a href="/shop/wishlist.html">Saved for later</a>`}
                <button type="button" onclick="Shop.logout()">Sign Out</button>
              ` : `
                <a href="/shop/login.html?role=customer">Customer Sign In</a>
                <a href="/shop/login.html?role=owner">Shop Owner Sign In</a>
                <a href="/shop/login.html?tab=register">Create Account</a>
              `}
            </div>
          </div>
          <a href="/shop/wishlist.html"><i class="fas fa-heart"></i> Saved <span class="cm-cart-count" id="wish-count">${this.getWishlist().length}</span></a>
          <a href="/shop/orders.html"><i class="fas fa-box"></i> Orders</a>
          <a href="/shop/cart.html"><i class="fas fa-shopping-cart"></i> Cart <span class="cm-cart-count" id="cart-count">${this.cartCount()}</span></a>
        </div>
      </div>
      <nav class="cm-nav">
        <div class="cm-nav-inner">
          <a href="/shop/?cat=laptops">Laptops</a>
          <a href="/shop/?cat=phones">Phones</a>
          <a href="/shop/?cat=audio">Audio</a>
          <a href="/shop/?cat=monitors">Monitors</a>
          <a href="/shop/?cat=accessories">Accessories</a>
          <a href="/shop/?cat=today">Today's Deals</a>
          ${this.isAdmin() ? '<a href="/shop/admin/">Seller Central</a>' : ''}
        </div>
      </nav>
    </header>`;
  },

  renderFooter() {
    return `
    <footer class="cm-footer">
      <div class="cm-footer-grid">
        <div>
          <h4>Get to Know Us</h4>
          <a href="#">About ShopZone</a>
          <a href="#">Careers</a>
        </div>
        <div>
          <h4>Make Money with Us</h4>
          <a href="/shop/admin/">Sell on ShopZone</a>
          <a href="/shop/admin/products.html">Add Products</a>
        </div>
        <div>
          <h4>Let Us Help You</h4>
          <a href="/shop/orders.html">Your Orders</a>
          <a href="/shop/cart.html">Your Cart</a>
          <a href="/shop/wishlist.html">Saved for later</a>
        </div>
        <div>
          <h4>AI Shopping</h4>
          <a href="#">Ask multi-agent assistant</a>
          <a href="/shop/login.html?role=owner">Owner Login</a>
        </div>
      </div>
      <div class="cm-footer-bottom">© 2026 ShopZone · Electronics retail + LangGraph agents</div>
    </footer>`;
  },

  mountLayout(activePage = 'home') {
    const headerEl = document.getElementById('shop-header');
    const footerEl = document.getElementById('shop-footer');
    if (headerEl) headerEl.innerHTML = this.renderHeader(activePage);
    if (footerEl) footerEl.innerHTML = this.renderFooter();
    this.updateCartCount();
    this.updateWishlistCount();

    document.addEventListener('click', (e) => {
      const dd = document.getElementById('account-dropdown');
      if (dd && !e.target.closest('.cm-header-actions') && !dd.contains(e.target)) {
        dd.classList.remove('show');
      }
    });
  },

  requireOwner() {
    if (!this.isLoggedIn() || !this.isAdmin()) {
      window.location.href = '/shop/login.html?role=owner&redirect=' + encodeURIComponent(window.location.pathname);
      return false;
    }
    return true;
  },

  requireCustomer() {
    if (!this.isLoggedIn()) {
      window.location.href = '/shop/login.html?role=customer&redirect=' + encodeURIComponent(window.location.pathname);
      return false;
    }
    return true;
  },

  handleSearch(e) {
    e.preventDefault();
    const q = document.getElementById('search-input')?.value || '';
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    window.location.href = '/shop/' + (params.toString() ? '?' + params.toString() : '');
    return false;
  },

  async fetchProducts() {
    const res = await fetch(`${this.API}/api/products?limit=100`);
    const data = await res.json();
    return data.products || [];
  },

  async fetchProduct(id) {
    const res = await fetch(`${this.API}/api/products/${id}`);
    if (!res.ok) throw new Error('Product not found');
    return res.json();
  },

  isElectronics(p) {
    return this.ELEC_CATS.includes((p.category || '').toLowerCase());
  },
};

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('shop-header')) Shop.mountLayout();
});
