import { useEffect, useRef, useState } from 'react'

/** Scroll-reveal / lazy module for automation (intersection observer) */
export default function LazyModule({ children, testId = 'lazy-module', className = '' }) {
  const ref = useRef(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        setVisible(true)
        io.disconnect()
      }
    }, { threshold: 0.15 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return (
    <div ref={ref} data-testid={testId} data-loaded={visible ? 'true' : 'false'} className={`lazy-sent ${visible ? 'visible' : ''} ${className}`}>
      {visible ? children : <div style={{ minHeight: 200 }} data-testid={`${testId}-placeholder`}>Loading module…</div>}
    </div>
  )
}
