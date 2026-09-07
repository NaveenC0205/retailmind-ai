import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { fetchProducts, formatPrice } from '../api'
import ProductCard from '../components/ProductCard'
import LazyModule from '../components/LazyModule'
import Loading from '../components/Loading'
import FilterDrawer from '../components/FilterDrawer'

const CAT_MAP = {
  iphone: 'phones',
  mac: 'laptops',
  audio: 'audio',
  monitors: 'monitors',
  accessories: 'accessories',
  phones: 'phones',
  laptops: 'laptops',
}

export default function StorePage({ homeModules = false }) {
  const { slug } = useParams()
  const [params] = useSearchParams()
  const [all, setAll] = useState([])
  const [loading, setLoading] = useState(true)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [filters, setFilters] = useState({
    cat: CAT_MAP[slug] || params.get('cat') || '',
    brand: '',
    minP: '',
    maxP: '',
    minR: '',
    feature: '',
    sort: 'rating',
    q: params.get('q') || '',
  })

  useEffect(() => {
    setLoading(true)
    fetchProducts().then(setAll).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (slug && CAT_MAP[slug]) setFilters((f) => ({ ...f, cat: CAT_MAP[slug] }))
  }, [slug])

  useEffect(() => {
    const qq = params.get('q') || ''
    const cc = params.get('cat') || ''
    setFilters((f) => ({
      ...f,
      q: qq || f.q,
      cat: cc ? (CAT_MAP[cc] || cc) : f.cat,
    }))
  }, [params])

  const elec = useMemo(
    () => all.filter((p) => ['laptops', 'phones', 'audio', 'monitors', 'accessories'].includes(p.category)),
    [all],
  )
  const brands = useMemo(() => [...new Set(elec.map((p) => p.brand))].sort(), [elec])

  const filtered = useMemo(() => {
    let rows = [...elec]
    const { cat, brand, q, minP, maxP, minR, feature, sort } = filters
    if (cat) rows = rows.filter((p) => p.category === cat)
    if (brand) rows = rows.filter((p) => p.brand === brand)
    if (q) {
      const qq = q.toLowerCase()
      rows = rows.filter((p) => `${p.title} ${p.brand} ${p.category} ${p.sku || ''}`.toLowerCase().includes(qq))
    }
    if (minP) rows = rows.filter((p) => p.price_inr >= Number(minP))
    if (maxP) rows = rows.filter((p) => p.price_inr <= Number(maxP))
    if (minR) rows = rows.filter((p) => p.rating >= Number(minR))
    if (feature === 'ram16') rows = rows.filter((p) => (p.attributes?.ram_gb || 0) >= 16)
    if (feature === 'ram32') rows = rows.filter((p) => (p.attributes?.ram_gb || 0) >= 32)
    if (feature === 'anc') rows = rows.filter((p) => p.attributes?.anc)
    if (sort === 'price_asc') rows.sort((a, b) => a.price_inr - b.price_inr)
    else if (sort === 'price_desc') rows.sort((a, b) => b.price_inr - a.price_inr)
    else rows.sort((a, b) => b.rating - a.rating)
    return rows
  }, [elec, filters])

  if (homeModules) {
    return (
      <div data-testid="home-page">
        <section className="sz-hero" data-testid="hero-module">
          <h1>iPhone</h1>
          <p>Meet the lineup. Designed for agentic & automation testing.</p>
          <div className="sz-hero-links">
            <Link to="/c/iphone" data-testid="hero-learn">Learn more</Link>
            <Link to="/store?cat=phones" data-testid="hero-shop">Shop iPhone</Link>
          </div>
          <div className="sz-hero-media">
            <img src="https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=1600&q=80" alt="iPhone hero" />
          </div>
        </section>
        <LazyModule testId="module-mac" className="sz-module">
          <h2>Mac</h2>
          <p className="lead">Supercharged notebooks for every workflow.</p>
          <div className="sz-hero-links">
            <Link to="/c/mac">Learn more</Link>
            <Link to="/store?cat=laptops">Buy</Link>
          </div>
        </LazyModule>
        <LazyModule testId="module-grid" className="sz-module gray">
          <h2>New arrivals</h2>
          <p className="lead">{elec.length}+ electronics SKUs</p>
          <div className="sz-wrap">
            <div className="sz-grid" data-testid="home-product-grid">
              {elec.slice(0, 8).map((p) => <ProductCard key={p.id} p={p} />)}
            </div>
            <p style={{ marginTop: 24 }}><Link to="/store" className="sz-btn sz-btn-blue">View all</Link></p>
          </div>
        </LazyModule>
      </div>
    )
  }

  const activeChips = [
    filters.cat && `Category: ${filters.cat}`,
    filters.brand && `Brand: ${filters.brand}`,
    (filters.minP || filters.maxP) && `Price: ₹${filters.minP || 0}–${filters.maxP || '∞'}`,
    filters.minR && `Rating ${filters.minR}+`,
    filters.feature && `Feature: ${filters.feature}`,
    filters.q && `Search: ${filters.q}`,
  ].filter(Boolean)

  return (
    <div className="sz-wrap" data-testid="store-page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 40, fontWeight: 600, letterSpacing: '-0.02em', margin: 0 }}>Store</h1>
          <p style={{ color: 'var(--muted)' }} data-testid="store-count">{filtered.length} products from catalogue</p>
        </div>
        <button type="button" className="sz-btn sz-btn-dark" data-testid="open-filters" onClick={() => setDrawerOpen(true)}>
          Filters
        </button>
      </div>

      {!!activeChips.length && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '12px 0 20px' }} data-testid="active-filters">
          {activeChips.map((c) => (
            <span key={c} className="sz-chip">{c}</span>
          ))}
        </div>
      )}

      <div className="sz-grid" data-testid="store-grid">
        {loading && <Loading label="Loading products from database…" testId="store-loading" />}
        {!loading && !filtered.length && <p data-testid="store-empty">No products match. Open Filters to change criteria.</p>}
        {!loading && filtered.map((p) => <ProductCard key={p.id} p={p} />)}
      </div>
      {!loading && filtered[0] && (
        <p className="sz-meta" style={{ marginTop: 16 }}>Sample price {formatPrice(filtered[0].price_inr)}</p>
      )}

      <FilterDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        brands={brands}
        value={filters}
        onApply={(next) => {
          setLoading(true)
          setFilters(next)
          // brief loading flash so automation can assert reload
          setTimeout(() => setLoading(false), 150)
        }}
      />
    </div>
  )
}
