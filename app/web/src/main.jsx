import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'
import Student from './pages/Student.jsx'
import Instructor from './pages/Instructor.jsx'
import './styles.css'
import TopBar from './components/TopBar.jsx'
import authService from './services/auth.js'

function Home() {
  const userInfo = authService.getUserInfo();
  const isAuthenticated = authService.isAuthenticated();

  // If authenticated via LTI, redirect to appropriate view
  if (isAuthenticated && userInfo) {
    const { courseId, isInstructor } = userInfo;
    const view = isInstructor ? 'instructor' : 'student';
    window.location.href = `/${view}?courseId=${courseId}`;
    return <div>Redirecting...</div>;
  }

  return (
    <>
      <TopBar />
      <div className="container">
        <div className="card">
          <h2 style={{marginTop:0}}>BluNote LTI - Local Demo</h2>
          {isAuthenticated ? (
            <div>
              <p>Welcome, {userInfo?.userName || 'User'}!</p>
              <p>Course: {userInfo?.courseTitle || userInfo?.courseId}</p>
              <p>Role: {userInfo?.isInstructor ? 'Instructor' : 'Student'}</p>
            </div>
          ) : (
            <>
              <p className="muted">Open one instructor and two students for the same course.</p>
              <div className="row" style={{ gap:24, flexWrap:'wrap' }}>
                <Link className="badge" to="/student?courseId=COURSE1&userId=stu1&name=Alice">Student: Alice</Link>
                <Link className="badge" to="/student?courseId=COURSE1&userId=stu2&name=Bob">Student: Bob</Link>
                <Link className="badge" to="/instructor?courseId=COURSE1&name=Prof">Instructor: COURSE1</Link>
              </div>
              <div className="space" />
              <div className="footnote">
                <p>For LTI integration, launch from your LMS.</p>
                <p>For local testing, pass <code>courseId</code>, <code>userId</code>, <code>name</code> as URL params.</p>
              </div>
            </>
          )}
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
