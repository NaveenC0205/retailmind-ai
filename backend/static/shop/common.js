/** Shared shop utilities — Amazon-style storefront */
const Shop = {
  API: '',

  getAuthToken() {
    return localStorage.getItem('auth_token');
  },

  getCustomerId() {
    return localStorage.getItem('customer_id') || 'CU-1001';
  },

  getAuthHeaders(json = false) {
    const headers = {};
    const token = this.getAuthToken();
    headers['Authorization'] = token ? `Bearer ${token}` : `Bearer customer:${this.getCustomerId()}`;
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
    this.toast(`Added to cart`);
  },

  logout() {
    ['auth_token', 'customer_id', 'customer_name', 'customer_email', 'is_admin'].forEach(k => localStorage.removeItem(k));
    window.location.href = '/shop/';
  },

  toast(message) {
    const t = document.createElement('div');
    t.className = 'amz-toast';
    t.textContent = message;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2500);
  },

  formatPrice(inr) {
    const s = inr.toLocaleString('en-IN');
    const parts = s.split(',');
    if (parts.length === 1) return `<span class="amz-price">₹${s}</span>`;
    const main = parts.slice(0, -1).join(',');
    const last = parts[parts.length - 1];
    return `<span class="amz-price">₹${main},<sup>${last}</sup></span>`;
  },

  stars(rating) {
    const full = Math.round(rating);
    return `<span class="amz-stars">${'★'.repeat(full)}${'☆'.repeat(5 - full)}</span>`;
  },

  productIcon(category) {
    return { laptops: 'laptop', phones: 'mobile-alt', audio: 'headphones', accessories: 'keyboard', monitors: 'desktop' }[category] || 'box';
  },

  productImageUrl(product) {
    const cat = (product.category || '').toLowerCase();
    const colors = { laptops: '667eea', phones: '764ba2', audio: 'f093fb', accessories: '4facfe', monitors: '43e97b' };
    const color = colors[cat] || 'cccccc';
    const icon = this.productIcon(cat);
    return `https://placehold.co/400x400/${color}/ffffff?text=${encodeURIComponent(product.brand || 'Product')}`;
  },

  toggleAccountMenu() {
    document.getElementById('account-dropdown')?.classList.toggle('show');
  },

  renderHeader(activePage = 'home') {
    const loggedIn = this.isLoggedIn();
    const name = localStorage.getItem('customer_name') || 'User';
    const greeting = loggedIn ? `Hello, ${name.split(' ')[0]}` : 'Hello, sign in';

    return `
    <header class="amz-header">
      <div class="amz-header-top">
        <a href="/shop/" class="amz-logo">shop<span>Zone</span></a>
        <div class="amz-deliver">
          <span>Deliver to</span>
          <strong>India</strong>
        </div>
        <form class="amz-search-wrap" onsubmit="return Shop.handleSearch(event)">
          <select class="amz-search-cat" id="search-category">
            <option value="">All</option>
            <option value="laptops">Laptops</option>
            <option value="phones">Phones</option>
            <option value="audio">Audio</option>
            <option value="accessories">Accessories</option>
          </select>
          <input type="text" class="amz-search-input" id="search-input" placeholder="Search ShopZone" value="">
          <button type="submit" class="amz-search-btn"><i class="fas fa-search"></i></button>
        </form>
        <div class="amz-header-actions">
          <div style="position:relative">
            <button class="amz-header-link" onclick="Shop.toggleAccountMenu()">
              <span>${greeting}</span>
              <strong>Account & Lists</strong>
            </button>
                <div id="account-dropdown" class="amz-dropdown">
              ${loggedIn ? `
                <div style="padding:10px 16px;font-size:12px;color:#666;border-bottom:1px solid #eee">${localStorage.getItem('customer_email') || ''}</div>
                ${this.isAdmin() ? `<a href="/shop/admin/">Seller Central</a><a href="/shop/admin/orders.html">Manage Orders</a><a href="/shop/admin/products.html">Manage Products</a>` : `<a href="/shop/orders.html">Your Orders</a><a href="/shop/cart.html">Your Cart</a>`}
                <button onclick="Shop.logout()">Sign Out</button>
              ` : `
                <a href="/shop/login.html?role=customer">Customer Sign In</a>
                <a href="/shop/login.html?role=owner">Shop Owner Sign In</a>
                <a href="/shop/login.html?tab=register">Create Account</a>
              `}
            </div>
          </div>
          <a href="/shop/orders.html" class="amz-header-link">
            <span>Returns</span>
            <strong>& Orders</strong>
          </a>
          <a href="/shop/cart.html" class="amz-cart-link">
            <i class="fas fa-shopping-cart fa-2x"></i>
            <span class="amz-cart-count" id="cart-count">${this.cartCount()}</span>
            <strong>Cart</strong>
          </a>
        </div>
      </div>
      <nav class="amz-nav">
        <div class="amz-nav-inner">
          <button class="amz-nav-link"><i class="fas fa-bars"></i> All</button>
          <a href="/shop/?cat=today" class="amz-nav-link">Today's Deals</a>
          <a href="/shop/?cat=laptops" class="amz-nav-link">Laptops</a>
          <a href="/shop/?cat=phones" class="amz-nav-link">Mobiles</a>
          <a href="/shop/?cat=audio" class="amz-nav-link">Electronics</a>
          <a href="/shop/?cat=accessories" class="amz-nav-link">Accessories</a>
          <a href="/shop/orders.html" class="amz-nav-link">Your Orders</a>
          <a href="/shop/admin/" class="amz-nav-link">Seller Central</a>
        </div>
      </nav>
    </header>`;
  },

  renderFooter() {
    return `
    <footer class="amz-footer">
      <div class="amz-footer-back" onclick="window.scrollTo({top:0,behavior:'smooth'})">Back to top</div>
      <div class="amz-footer-grid">
        <div><h4>Get to Know Us</h4><a href="#">About ShopZone</a><a href="#">Careers</a></div>
        <div><h4>Make Money with Us</h4><a href="/shop/admin/">Sell on ShopZone</a><a href="/shop/admin/products.html">Add Products</a></div>
        <div><h4>Let Us Help You</h4><a href="/shop/orders.html">Your Orders</a><a href="/shop/cart.html">Your Cart</a></div>
      </div>
      <div class="amz-footer-bottom">© 2026 ShopZone. Built for agentic AI testing.</div>
    </footer>`;
  },

  mountLayout(activePage = 'home') {
    const headerEl = document.getElementById('shop-header');
    const footerEl = document.getElementById('shop-footer');
    if (headerEl) headerEl.innerHTML = this.renderHeader(activePage);
    if (footerEl) footerEl.innerHTML = this.renderFooter();
    this.updateCartCount();

    document.addEventListener('click', (e) => {
      const dd = document.getElementById('account-dropdown');
      if (dd && !e.target.closest('.amz-header-link') && !dd.contains(e.target)) {
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
    const cat = document.getElementById('search-category')?.value || '';
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (cat) params.set('cat', cat);
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
  }
};

document.addEventListener('DOMContentLoaded', () => Shop.mountLayout());
