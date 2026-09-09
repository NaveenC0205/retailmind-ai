import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { fetchProducts, askAgent } from '../api'
import DeviceScene from '../components/DeviceScene'
import ProductCard from '../components/ProductCard'
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
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
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
    let active = true
    setLoading(true)
    setError('')
    fetchProducts().then((rows) => { if (active) setAll(rows) }).catch((e) => { if (active) setError(e.message) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [retry])

  const elec = all
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

  const loadState = error ? <div className="sz-empty" role="alert"><h2>We couldn’t load the catalogue</h2><p>{error}</p><button className="sz-btn sz-btn-blue" onClick={() => setRetry((n) => n + 1)}>Try again</button></div> : loading ? <Loading label="Loading products…" testId="store-loading" /> : null
  if (homeModules) {
    return <div data-testid="home-page" className="sz-home">
      <section className="sz-discover-hero" data-testid="hero-module">
        <div className="sz-discover-copy"><span className="sz-eyebrow">DISCOVER A NEW DIMENSION</span><h1>Your world.<br /><span>Upgraded.</span></h1><p>Discover tech and more, compare the details, and find the right fit with your AI shopping assistant.</p><div className="sz-hero-ctas"><Link to="/store" className="sz-btn sz-btn-blue" data-testid="hero-shop">Explore the store ↗</Link><button type="button" className="sz-btn sz-btn-ghost" onClick={() => askAgent('Help me find a laptop under 80000')}>✦ Ask the assistant</button></div><Link to="/lab" className="sz-hero-lab" data-testid="hero-learn">Learning AI testing? Enter the Test Lab →</Link></div>
        <DeviceScene />
      </section>
      <nav className="sz-category-cards" aria-label="Shop by category">{[['phones','01','Phones'],['laptops','02','Laptops'],['audio','03','Audio'],['monitors','04','Monitors'],['accessories','05','Accessories']].map(([id,n,label]) => <Link key={id} to={`/c/${id}`} data-depth-card><span>{n}</span><strong>{label}</strong><span aria-hidden="true">↗</span></Link>)}</nav>
      <section className="sz-featured" data-testid="module-grid"><div className="sz-section-head"><div><span className="sz-eyebrow">THE EDIT</span><h2>Discover the catalogue</h2></div><Link to="/store">View all products →</Link></div>{loadState}<div className="sz-grid" data-testid="home-product-grid">{!loading && !error && elec.slice(0,8).map((p) => <ProductCard key={p.id} p={p} />)}</div></section>
      <section className="sz-explore-lab" data-testid="module-mac"><div><span className="sz-eyebrow">BEYOND THE STOREFRONT</span><h2>Learn AI by<br />putting it to the test.</h2><p>Explore RAG answers, compare agents, inspect their actions, and build evidence for your next test report.</p><Link to="/lab" className="sz-btn sz-btn-blue">Open the AI workshop ↗</Link></div><div className="sz-lab-preview"><div><span>01</span><strong>Ask & compare</strong><p>One question. Four execution modes.</p></div><div><span>02</span><strong>Inspect the evidence</strong><p>Policy sources, tool use, and conversation context.</p></div><div><span>03</span><strong>Record your verdict</strong><p>Review results and export your findings.</p></div></div></section>
    </div>
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
          <span className="sz-eyebrow">FIND YOUR NEXT FAVORITE</span><h1>{filters.cat ? filters.cat.charAt(0).toUpperCase() + filters.cat.slice(1) : 'Explore the store'}</h1>
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

      {error && loadState}
      <div className="sz-grid" data-testid="store-grid">
        {loading && <Loading label="Loading products from database…" testId="store-loading" />}
        {!loading && !error && !filtered.length && <p data-testid="store-empty">No products match. Open Filters to change criteria.</p>}
        {!loading && !error && filtered.map((p) => <ProductCard key={p.id} p={p} />)}
      </div>
      <FilterDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        brands={brands}
        categories={[...new Set(all.map((p) => p.category))].sort()}
        value={filters}
        onApply={(next) => {
          setFilters(next)
          // State owns all filters during this visit; navigation starts fresh.

        }}
      />
    </div>
  )
}
