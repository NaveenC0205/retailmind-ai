import { useRef, useState } from 'react'
import { productImage } from '../api'

const views = [['Front', 0, -12], ['Right', -90, -12], ['Back', 180, -12], ['Left', 90, -12], ['Top', 0, -85], ['Bottom', 0, 85]]
function Box({ className, children }) {
  return <div className={`sz-model-box ${className}`}><div className="face front">{children}</div><div className="face back"/><div className="face left"/><div className="face right"/><div className="face top"/><div className="face bottom"/></div>
}
function Model({ category }) {
  return <div className={`sz-product-model model-${category}`} aria-hidden="true">{category === 'laptops' ? <><Box className="model-lid"><div className="model-screen">Explore every angle</div></Box><Box className="model-deck"/></> : <Box className="model-handset"><div className="model-screen">09:41</div></Box>}</div>
}

export default function ProductViewer({ product }) {
  const supportsModel = ['laptops', 'phones'].includes(product.category)
  const [isModel, setIsModel] = useState(supportsModel)
  const [angle, setAngle] = useState(25)
  const [elevation, setElevation] = useState(-12)
  const [zoom, setZoom] = useState(1)
  const drag = useRef(null)
  const hasImage = Boolean(product.image_url) || ['phones', 'laptops', 'audio', 'monitors', 'accessories'].includes(product.category)
  return <section className="sz-product-viewer" aria-label="Interactive product view" data-testid="pdp-viewer">
    {supportsModel && <div className="sz-view-buttons" role="group" aria-label="Viewer mode"><button type="button" aria-pressed={isModel} onClick={() => setIsModel(true)}>360° illustration</button><button type="button" aria-pressed={!isModel} onClick={() => setIsModel(false)}>Photo</button></div>}
    <div className="sz-product-viewer-stage" tabIndex={isModel ? 0 : undefined} role={isModel ? 'group' : undefined} aria-label={isModel ? 'Rotate illustration with left and right arrow keys' : 'Product photo'}
      onKeyDown={(event) => { if (isModel && ['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); setAngle(value => value + (event.key === 'ArrowLeft' ? -15 : 15)) } }}
      onPointerDown={(event) => { if (!isModel || event.button !== 0) return; drag.current = { x: event.clientX, angle }; event.currentTarget.setPointerCapture(event.pointerId) }}
      onPointerMove={(event) => { if (drag.current) setAngle(drag.current.angle + (event.clientX - drag.current.x) * .6) }}
      onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }} onLostPointerCapture={() => { drag.current = null }}>
      <div className="sz-product-viewer-halo" aria-hidden="true" />
      {isModel ? <div className="sz-product-model-orbit" style={{ transform: `scale(${zoom}) rotateX(${elevation}deg) rotateY(${angle}deg)` }}><Model category={product.category}/></div> : <div className="sz-product-viewer-plane" style={{ transform: `scale(${zoom})` }}>
        {hasImage ? <img src={productImage(product)} alt={product.image_url ? product.title : `Illustrative ${product.category} image`} draggable="false" data-testid="pdp-image" /> : <div className="sz-product-viewer-placeholder"><span>{product.category}</span><strong>{product.title}</strong><p>Product photo not available</p></div>}
      </div>}
      <span className="sz-product-viewer-hint">{isModel ? 'Drag sideways for 360° · scroll vertically to explore' : 'Use zoom to look closer'}</span>
    </div>
    {isModel && <div className="sz-view-buttons" role="group" aria-label="Product angles">{views.map(([label, yaw, pitch]) => <button type="button" key={label} aria-pressed={((angle % 360) + 360) % 360 === ((yaw % 360) + 360) % 360 && elevation === pitch} onClick={() => { setAngle(yaw); setElevation(pitch) }}>{label}</button>)}</div>}
    <div className="sz-product-viewer-controls">
      {isModel && <><label>Rotate<input type="range" aria-label="Rotate product illustration" min="0" max="359" value={((angle % 360) + 360) % 360} onChange={(event) => setAngle(Number(event.target.value))} /></label><label>Elevation<input type="range" aria-label="Product view elevation" min="-90" max="90" value={elevation} onChange={(event) => setElevation(Number(event.target.value))} /></label></>}
      <div role="group" aria-label="Image zoom"><button type="button" aria-label="Zoom out" disabled={zoom <= .8} onClick={() => setZoom((value) => Math.max(.8, +(value - .1).toFixed(1)))}>−</button><output aria-live="polite">{Math.round(zoom * 100)}%</output><button type="button" aria-label="Zoom in" disabled={zoom >= 1.4} onClick={() => setZoom((value) => Math.min(1.4, +(value + .1).toFixed(1)))}>+</button></div>
      <button type="button" onClick={() => { setAngle(25); setElevation(-12); setZoom(1) }}>Reset</button>
    </div>
    <p className="sz-product-viewer-note">{isModel ? 'Generic 3D illustration, not an exact model of this product. Colours, ports and proportions may differ.' : product.image_url ? 'Product image. Additional angle photos have not been supplied.' : hasImage ? 'Illustrative category photo. Actual product angle photos have not been supplied.' : 'Product photos have not been supplied.'}</p>
  </section>
}
