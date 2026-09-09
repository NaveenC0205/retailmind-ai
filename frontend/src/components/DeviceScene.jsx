import { useState } from 'react'

/** Lightweight, CSS-built 3D objects; no remote models or rendering dependency. */
export default function DeviceScene({ compact = false }) {
  const [rotation, setRotation] = useState(-12)
  const [color, setColor] = useState('blue')
  return <div className={`sz-device-showcase ${compact ? 'compact' : ''}`} data-testid="device-showcase">
    <div className="sz-device-stage" aria-hidden="true" style={{ '--scene-rotation': `${rotation}deg`, '--device-accent': color === 'blue' ? '#77c6ff' : '#c1a2ff' }}>
      <div className="sz-stage-halo" /><div className="sz-stage-grid" />
      <div className="sz-device-world">
        <div className="sz-orbit-ring ring-one" /><div className="sz-orbit-ring ring-two" />
        <div className="sz-pedestal"><span /></div>
        <div className="sz-laptop-3d">
          <div className="sz-laptop-screen"><div className="sz-screen-inner"><div className="sz-screen-orb"/><div className="sz-screen-arc"/><div className="sz-screen-caption">Make room<br />for possibility.</div><div className="sz-screen-dock">{Array.from({ length: 5 }, (_, i) => <i key={i}/>)}</div></div></div>
          <div className="sz-laptop-base"><div className="sz-laptop-keys"/><div className="sz-laptop-trackpad"/></div>
        </div>
        <div className="sz-phone-3d"><div className="sz-phone-back"/><div className="sz-phone-edge"/><div className="sz-phone-face"><div className="sz-phone-camera"/><span>09:41</span><div className="sz-phone-orb"/><div className="sz-phone-widgets"><i/><i/></div><div className="sz-phone-home"/></div></div>
        <div className="sz-floating-cube"><i/><i/><i/></div>
        <div className="sz-scene-bead bead-one"/><div className="sz-scene-bead bead-two"/>
      </div>
      <span className="sz-scene-label label-top">DESIGNED FOR YOUR EVERYDAY</span>
      <span className="sz-scene-label label-bottom">A fresh perspective.</span>
    </div>
    {!compact && <div className="sz-scene-controls">
      <label>Rotate the view<input aria-label="Rotate the 3D display" type="range" min="-30" max="30" value={rotation} onChange={(event) => setRotation(Number(event.target.value))}/></label>
      <div className="sz-scene-swatches" role="group" aria-label="Display color"><button type="button" className="blue" aria-label="Blue display" aria-pressed={color === 'blue'} onClick={() => setColor('blue')}/><button type="button" className="violet" aria-label="Violet display" aria-pressed={color === 'violet'} onClick={() => setColor('violet')}/></div>
    </div>}
  </div>
}
