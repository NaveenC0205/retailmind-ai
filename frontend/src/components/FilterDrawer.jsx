import { useEffect, useMemo, useRef, useState } from 'react'

/** Side drawer filters — apply reloads listing from in-memory DB catalogue */
export default function FilterDrawer({
  open,
  onClose,
  brands = [],
  categories = [],
  value,
  onApply,
}) {
  const [local, setLocal] = useState(value)
  const panel = useRef(null)

  useEffect(() => {
    if (open) setLocal(value)
  }, [open, value])

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    panel.current?.querySelector('button')?.focus()
    const onKey = (event) => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'Tab') {
        const controls = panel.current?.querySelectorAll('button, input, select, textarea, a[href]')
        if (!controls?.length) return
        const first = controls[0], last = controls[controls.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = overflow; previous?.focus() }
  }, [open, onClose])

  const summary = useMemo(() => {
    const parts = []
    if (local.cat) parts.push(local.cat)
    if (local.brand) parts.push(local.brand)
    if (local.minP || local.maxP) parts.push(`₹${local.minP || 0}–${local.maxP || '∞'}`)
    if (local.minR) parts.push(`★${local.minR}+`)
    if (local.feature) parts.push(local.feature)
    return parts.join(' · ') || 'No filters'
  }, [local])

  if (!open) return null

  return (
    <div className="sz-drawer-root" data-testid="filter-drawer">
      <button type="button" className="sz-drawer-backdrop" data-testid="filter-backdrop" aria-label="Close filters" onClick={onClose} />
      <aside className="sz-drawer" ref={panel} role="dialog" aria-modal="true" aria-label="Filters">
        <div className="sz-drawer-head">
          <h2>Filters</h2>
          <button type="button" className="sz-btn sz-btn-ghost" data-testid="filter-close" onClick={onClose}>Close</button>
        </div>
        <p className="sz-meta" data-testid="filter-summary">{summary}</p>

        <label className="sz-meta">Category</label>
        <select className="sz-input" data-testid="drawer-cat" value={local.cat || ''} onChange={(e) => setLocal({ ...local, cat: e.target.value })}>
          <option value="">All categories</option>
          {categories.map((category) => <option key={category} value={category}>{category}</option>)}
        </select>

        <label className="sz-meta">Brand</label>
        <select className="sz-input" data-testid="drawer-brand" value={local.brand || ''} onChange={(e) => setLocal({ ...local, brand: e.target.value })}>
          <option value="">Any brand</option>
          {brands.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>

        <label className="sz-meta">Min price ₹</label>
        <input className="sz-input" data-testid="drawer-min" type="number" value={local.minP || ''} onChange={(e) => setLocal({ ...local, minP: e.target.value })} />
        <label className="sz-meta">Max price ₹</label>
        <input className="sz-input" data-testid="drawer-max" type="number" value={local.maxP || ''} onChange={(e) => setLocal({ ...local, maxP: e.target.value })} />

        <label className="sz-meta">Min rating</label>
        <select className="sz-input" data-testid="drawer-rating" value={local.minR || ''} onChange={(e) => setLocal({ ...local, minR: e.target.value })}>
          <option value="">Any</option>
          <option value="4.5">4.5+</option>
          <option value="4">4+</option>
          <option value="3.5">3.5+</option>
        </select>

        <label className="sz-meta">Feature</label>
        <select className="sz-input" data-testid="drawer-feature" value={local.feature || ''} onChange={(e) => setLocal({ ...local, feature: e.target.value })}>
          <option value="">Any</option>
          <option value="ram16">RAM 16GB+</option>
          <option value="ram32">RAM 32GB+</option>
          <option value="anc">ANC</option>
        </select>

        <label className="sz-meta">Sort</label>
        <select className="sz-input" data-testid="drawer-sort" value={local.sort || 'rating'} onChange={(e) => setLocal({ ...local, sort: e.target.value })}>
          <option value="rating">Top rated</option>
          <option value="price_asc">Price ↑</option>
          <option value="price_desc">Price ↓</option>
        </select>

        <div style={{ display: 'flex', gap: 8, marginTop: 20 }}>
          <button
            type="button"
            className="sz-btn sz-btn-blue sz-btn-full"
            data-testid="filter-apply"
            onClick={() => { onApply(local); onClose() }}
          >
            Apply filters
          </button>
        </div>
        <button
          type="button"
          className="sz-btn sz-btn-ghost sz-btn-full"
          style={{ marginTop: 8 }}
          data-testid="filter-clear"
          onClick={() => {
            const cleared = { cat: '', brand: '', minP: '', maxP: '', minR: '', feature: '', sort: 'rating', q: '' }
            setLocal(cleared)
            onApply(cleared)
            onClose()
          }}
        >
          Clear all
        </button>
      </aside>
    </div>
  )
}
