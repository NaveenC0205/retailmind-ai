import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchProducts } from '../api'

/** Autocomplete search over titles, brands, categories, SKUs from DB catalogue */
export default function SearchBox({ compact = false }) {
  const [q, setQ] = useState('')
  const [cat, setCat] = useState('')
  const [all, setAll] = useState([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const listId = useId()
  const wrapRef = useRef(null)
  const nav = useNavigate()

  useEffect(() => {
    fetchProducts().then(setAll).catch(() => setAll([]))
  }, [])

  useEffect(() => {
    const onDoc = (e) => {
      if (!wrapRef.current?.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const suggestions = useMemo(() => {
    const qq = q.trim().toLowerCase()
    const pool = all
    const filtered = cat ? pool.filter((p) => p.category === cat) : pool
    if (!qq) {
      // show popular options
      const cats = [...new Set(pool.map((p) => p.category))].map((c) => ({ type: 'category', label: c, value: c }))
      const brands = [...new Set(pool.map((p) => p.brand))].slice(0, 8).map((b) => ({ type: 'brand', label: b, value: b }))
      return [...cats, ...brands].slice(0, 12)
    }
    const out = []
    for (const p of filtered) {
      const hay = `${p.title} ${p.brand} ${p.category} ${p.sku || ''}`.toLowerCase()
      if (!hay.includes(qq)) continue
      out.push({ type: 'product', label: p.title, value: p.title, id: p.id, brand: p.brand, category: p.category })
      if (out.length >= 10) break
    }
    // also brand/category hits
    for (const b of [...new Set(filtered.map((p) => p.brand))]) {
      if (b.toLowerCase().includes(qq)) out.push({ type: 'brand', label: `Brand: ${b}`, value: b })
    }
    for (const c of [...new Set(filtered.map((p) => p.category))]) {
      if (c.toLowerCase().includes(qq)) out.push({ type: 'category', label: `Category: ${c}`, value: c })
    }
    return out.slice(0, 12)
  }, [all, q, cat])

  function go(s) {
    setOpen(false)
    if (s?.type === 'product' && s.id) {
      nav(`/product/${s.id}`)
      return
    }
    const params = new URLSearchParams()
    if (s?.type === 'brand') params.set('q', s.value)
    else if (s?.type === 'category') params.set('cat', s.value)
    else if (q.trim()) params.set('q', q.trim())
    if (cat && s?.type !== 'category') params.set('cat', cat)
    nav(`/store?${params.toString()}`)
  }

  function onSubmit(e) {
    e.preventDefault()
    if (open && active >= 0 && suggestions[active]) go(suggestions[active])
    else go({ type: 'query', value: q })
  }

  return (
    <div className={`sz-search-wrap ${compact ? 'compact' : ''}`} ref={wrapRef} data-testid="search-autocomplete">
      <form className="sz-search" onSubmit={onSubmit} data-testid="nav-search-form" autoComplete="off">
        <select data-testid="nav-search-cat" value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Category">
          <option value="">All</option>
          <option value="phones">Phones</option>
          <option value="laptops">Laptops</option>
          <option value="audio">Audio</option>
          <option value="monitors">Monitors</option>
          <option value="accessories">Accessories</option>
        </select>
        <input
          data-testid="nav-search-input"
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(-1) }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (!open) return
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, suggestions.length - 1)) }
            if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
            if (e.key === 'Escape') setOpen(false)
          }}
          placeholder="Search products, brands, categories…"
          aria-label="Search"
          role="combobox"
          aria-controls={listId}
          aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
          aria-autocomplete="list"
          aria-expanded={open}
        />
        <button type="submit" data-testid="nav-search-btn" aria-label="Search">⌕</button>
      </form>
      {open && suggestions.length > 0 && (
        <ul className="sz-ac-list" data-testid="search-suggestions" role="listbox" id={listId}>
          {suggestions.map((s, i) => (
            <li
              key={`${s.type}-${s.label}-${i}`}
              role="option"
              id={`${listId}-${i}`}
              aria-selected={i === active}
              className={i === active ? 'active' : ''}
              data-testid={`search-suggestion-${i}`}
              onMouseDown={(e) => { e.preventDefault(); go(s) }}
            >
              <span className="sz-ac-type">{s.type}</span>
              <span>{s.label}</span>
              {s.brand && <span className="sz-meta"> · {s.brand}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
