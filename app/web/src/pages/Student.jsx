import React, { useEffect, useMemo, useState } from 'react'
import { io } from 'socket.io-client'
import TopBar from '../components/TopBar'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:4000'

function useQuery() {
  return useMemo(() => new URLSearchParams(window.location.search), [])
}

export default function Student() {
  const query = useQuery()
  const courseId = query.get('courseId') || 'COURSE1'
  const userId = query.get('userId') || 'student'
  const name = query.get('name') || userId
  const [socket, setSocket] = useState(null)
  const [connected, setConnected] = useState(false)
  const [metrics, setMetrics] = useState(null)
  const [tutoring, setTutoring] = useState(null)
  const [pressedAt, setPressedAt] = useState(0)
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    const s = io(API_BASE, {
      transports: ['websocket'],
      query: { role: 'student', courseId, userId, name },
    })
    s.on('connect', () => setConnected(true))
    s.on('disconnect', () => setConnected(false))
    s.on('metrics', (m) => setMetrics(m))
    s.on('tutoring', (content) => setTutoring(content))
    setSocket(s)
    return () => s.disconnect()
  }, [courseId, userId, name])

  const onConfused = () => {
    socket?.emit('confused')
    setPressedAt(Date.now())
  }

  useEffect(() => {
    const t = setInterval(() => {
      const debounce = metrics?.debounceSec ?? 20
      const left = Math.max(0, debounce - Math.floor((Date.now() - pressedAt) / 1000))
      setCooldown(left)
      // Send presence heartbeat every tick if connected
      if (socket && socket.connected) {
        socket.emit('presence')
      }
    }, 250)
    return () => clearInterval(t)
  }, [socket, metrics?.debounceSec, pressedAt])

  return (
    <>
      <TopBar right={<span className="badge">{connected ? 'Connected' : 'Connecting…'}</span>} />
      <div className="container">
        <div className="card">
          <h2 style={{marginTop:0}}>Student</h2>
          <div className="muted">Course: <b>{courseId}</b> · User: <b>{name}</b></div>

          <div className="space" />
          <button className="primary-btn" onClick={onConfused} disabled={cooldown>0}>
            {cooldown>0 ? `Try again in ${cooldown}s` : 'I’m Confused'}
          </button>
          <div className="footnote" style={{marginTop:8}}>Press is anonymous and aggregated.</div>

          {metrics && (
            <div className="space">
            </div>
          )}
          {metrics && (
            <div className="stat-grid">
              <div className="stat"><div className="label">Unique in {metrics.windowSec}s</div><div className="value">{metrics.uniqueCount}</div></div>
              <div className="stat"><div className="label">Roster</div><div className="value">{metrics.roster}</div></div>
              <div className="stat"><div className="label">Class Confusion</div><div className="value">{metrics.pct}%</div></div>
            </div>
          )}
        </div>

        {tutoring && (
          <div className="card tutoring" style={{marginTop:16}}>
            <h3>Personal Tutoring</h3>
            <h4 style={{marginTop:0}}>{tutoring.title}</h4>
            <p>{tutoring.text}</p>
            <ol>
              {tutoring.practice?.map((p, idx) => (
                <li key={idx}>
                  <div>{p.q}</div>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </>
  )
}
