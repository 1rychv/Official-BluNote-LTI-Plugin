import React from 'react'

export default function MeterBar({ pct = 0, threshold = 25 }) {
  const clamped = Math.max(0, Math.min(100, Math.round(pct)))
  const showCenter = clamped >= 6 && clamped <= 94 // avoid overlapping 0%/100%
  return (
    <div className="meter-wrap">
      <div className="meter">
        <div
          className="fill"
          style={{
            width: `${clamped}%`,
            background: clamped >= threshold ? 'linear-gradient(90deg,#ef4444,#f59e0b)' : undefined,
          }}
        />
        <div className="threshold" style={{ left: `${threshold}%` }} />
      </div>
      <div className="meter-label left">0%</div>
      {showCenter && (
        <div className="meter-label center" style={{ left: `${clamped}%` }}>{clamped}%</div>
      )}
      <div className="meter-label right">100%</div>
    </div>
  )
}
