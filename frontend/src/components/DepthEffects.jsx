import { useEffect, useState } from 'react'

export default function DepthEffects() {
  const [enabled, setEnabled] = useState(() => {
    try { return localStorage.getItem('sz-depth-motion') !== 'off' } catch { return true }
  })
  useEffect(() => {
    const preference = matchMedia('(prefers-reduced-motion: reduce)')
    const pointer = matchMedia('(hover: hover) and (pointer: fine)')
    let active = null, frame = null
    const reset = () => {
      if (frame !== null) cancelAnimationFrame(frame)
      frame = null
      if (active) { active.style.removeProperty('--tilt-x'); active.style.removeProperty('--tilt-y') }
      active = null
    }
    const sync = () => { document.documentElement.dataset.depthMotion = enabled && !preference.matches ? 'on' : 'off'; reset() }
    sync()
    const move = (event) => {
      if (!enabled || preference.matches || !pointer.matches) return
      const target = event.target.closest?.('[data-depth-card]')
      if (target !== active) { reset(); active = target }
      if (!active) return
      const rect = active.getBoundingClientRect()
      const x = Math.max(-1, Math.min(1, (event.clientX - rect.left) / rect.width * 2 - 1))
      const y = Math.max(-1, Math.min(1, (event.clientY - rect.top) / rect.height * 2 - 1))
      if (frame !== null) cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => { active?.style.setProperty('--tilt-x', `${-y * 3}deg`); active?.style.setProperty('--tilt-y', `${x * 4}deg`); frame = null })
    }
    document.addEventListener('pointermove', move, { passive: true })
    document.addEventListener('pointerleave', reset)
    window.addEventListener('blur', reset)
    preference.addEventListener('change', sync)
    pointer.addEventListener('change', reset)
    try { localStorage.setItem('sz-depth-motion', enabled ? 'on' : 'off') } catch { /* A preference is optional. */ }
    return () => { reset(); document.removeEventListener('pointermove', move); document.removeEventListener('pointerleave', reset); window.removeEventListener('blur', reset); preference.removeEventListener('change', sync); pointer.removeEventListener('change', reset) }
  }, [enabled])
  return <button type="button" className="sz-motion-control" aria-pressed={enabled} onClick={() => setEnabled((value) => !value)} title="Reduced-motion device settings are always respected">◈ Motion {enabled ? 'on' : 'off'}</button>
}
