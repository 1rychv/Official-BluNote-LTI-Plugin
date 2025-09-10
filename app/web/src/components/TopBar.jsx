import React from 'react'

export default function TopBar({ right }){
  return (
    <div className="topbar">
      <div className="brand"><span className="brand-dot"/>BluNote</div>
      <div>{right}</div>
    </div>
  )
}

