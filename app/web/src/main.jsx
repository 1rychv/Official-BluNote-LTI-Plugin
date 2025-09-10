import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'
import Student from './pages/Student.jsx'
import Instructor from './pages/Instructor.jsx'
import './styles.css'
import TopBar from './components/TopBar.jsx'

function Home() {
  return (
    <>
      <TopBar />
      <div className="container">
        <div className="card">
          <h2 style={{marginTop:0}}>Local Demo</h2>
          <p className="muted">Open one instructor and two students for the same course.</p>
          <div className="row" style={{ gap:24, flexWrap:'wrap' }}>
            <Link className="badge" to="/student?courseId=COURSE1&userId=stu1&name=Alice">Student: Alice</Link>
            <Link className="badge" to="/student?courseId=COURSE1&userId=stu2&name=Bob">Student: Bob</Link>
            <Link className="badge" to="/instructor?courseId=COURSE1&name=Prof">Instructor: COURSE1</Link>
          </div>
          <div className="space" />
          <div className="footnote">Pass <code>courseId</code>, <code>userId</code>, <code>name</code> as URL params to simulate different users.</div>
        </div>
      </div>
    </>
  )}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/student" element={<Student />} />
        <Route path="/instructor" element={<Instructor />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
)
