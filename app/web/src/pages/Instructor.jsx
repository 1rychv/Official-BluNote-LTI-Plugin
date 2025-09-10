import React, { useEffect, useMemo, useState } from 'react'
import { io } from 'socket.io-client'
import axios from 'axios'
import TopBar from '../components/TopBar'
import MeterBar from '../components/MeterBar'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:4000'

function useQuery() {
  return useMemo(() => new URLSearchParams(window.location.search), [])
}

export default function Instructor() {
  const query = useQuery()
  const courseId = query.get('courseId') || 'COURSE1'
  const name = query.get('name') || 'Instructor'
  const [socket, setSocket] = useState(null)
  const [connected, setConnected] = useState(false)
  const [metrics, setMetrics] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [roster, setRoster] = useState(20)
  const [savingRoster, setSavingRoster] = useState(false)

  useEffect(() => {
    const s = io(API_BASE, {
      transports: ['websocket'],
      query: { role: 'instructor', courseId, name },
    })
    s.on('connect', () => setConnected(true))
    s.on('disconnect', () => setConnected(false))
    s.on('metrics', (m) => setMetrics(m))
    s.on('trigger', (t) => setAlerts((prev) => [{ at: t.at, pct: t.pct }, ...prev].slice(0, 5)))
    setSocket(s)
    return () => s.disconnect()
  }, [courseId, name])

  useEffect(() => {
    axios.get(`${API_BASE}/api/course/${courseId}/roster`).then(res => setRoster(res.data.roster))
  }, [courseId])

  const saveRoster = async () => {
    setSavingRoster(true)
    try {
      await axios.post(`${API_BASE}/api/course/${courseId}/roster`, { roster: Number(roster) })
    } finally {
      setSavingRoster(false)
    }
  }

  return (
    <>
      <TopBar right={<span className="badge">{connected ? 'Connected' : 'Connecting…'}</span>} />
      <div className="container">
        <div className="card">
          <h2 style={{marginTop:0}}>Instructor Dashboard</h2>
          <div className="muted">Course: <b>{courseId}</b> · User: <b>{name}</b></div>

          {metrics && (
            <>
              <div className="space" />
              <MeterBar pct={metrics.pct} threshold={metrics.threshold} />
              <div className="space" />
              {metrics.pct >= metrics.threshold && (
                <div className="banner">Confusion above threshold — consider pausing to explain.</div>
              )}
              <div className="space" />
              <div className="stat-grid">
                <div className="stat"><div className="label">Unique in {metrics.windowSec}s</div><div className="value">{metrics.uniqueCount}</div></div>
                <div className="stat"><div className="label">Roster ({metrics.rosterSource === 'auto' ? 'auto' : 'manual'})</div><div className="value">{metrics.roster}</div></div>
                <div className="stat"><div className="label">Percent</div><div className="value" style={{color: metrics.pct >= metrics.threshold ? 'var(--danger)' : 'var(--text)'}}>{metrics.pct}%</div></div>
              </div>
            </>
          )}

          <div className="space" />
          <div className="row">
            <label className="muted">Roster size (manual override)</label>
            <input type="number" value={roster} onChange={e => setRoster(e.target.value)} style={{ width: 100, padding:'6px 8px', borderRadius:8, border:'1px solid var(--border)', background:'transparent', color:'var(--text)' }} />
            <button className="badge" onClick={saveRoster} disabled={savingRoster}>{savingRoster ? 'Saving…' : 'Save'}</button>
          </div>
        </div>

        <div className="card">
          <h3 style={{marginTop:0}}>Alerts</h3>
          {alerts.length === 0 && <div className="muted">No triggers yet.</div>}
          <ul>
            {alerts.map((a, i) => (
              <li key={i} className="muted">[{new Date(a.at).toLocaleTimeString()}] Confusion exceeded threshold ({a.pct}%)</li>
            ))}
          </ul>
        </div>
      </div>
    </>
  )}
