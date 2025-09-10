require('dotenv').config();
const express = require('express');
const http = require('http');
const cors = require('cors');
const { Server } = require('socket.io');

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 4000;
const ALLOWED_ORIGIN = process.env.ALLOWED_ORIGIN || 'http://localhost:5173';

const THRESHOLD_PERCENT = Number(process.env.THRESHOLD_PERCENT || 25);
const WINDOW_MS = 1000 * Number(process.env.WINDOW_SEC || 120);
const DEBOUNCE_MS = 1000 * Number(process.env.DEBOUNCE_SEC || 20);
const COOLDOWN_MS = 1000 * Number(process.env.COOLDOWN_SEC || 180);

// Basic CORS for local dev
app.use(cors({ origin: ALLOWED_ORIGIN, credentials: true }));

// In-memory state (dev). Replace with Redis/Postgres in production.
const state = {
  courses: new Map(), // courseId -> { roster, presses: [{userId, ts}], lastPressByUser: Map, lastTriggerAt, tutoringByUser: Map }
  socketsByUser: new Map(), // userId -> Set<socket.id>
};

function getCourse(courseId) {
  if (!state.courses.has(courseId)) {
    state.courses.set(courseId, {
      roster: 20, // default dev roster
      presses: [], // array of { userId, ts }
      lastPressByUser: new Map(),
      lastTriggerAt: 0,
      tutoringByUser: new Map(), // userId -> latest tutoring content
    });
  }
  return state.courses.get(courseId);
}

function pruneOld(course, now) {
  const horizon = now - WINDOW_MS;
  // Keep only events inside window
  let i = 0;
  while (i < course.presses.length && course.presses[i].ts < horizon) i++;
  if (i > 0) course.presses.splice(0, i);
}

function uniqueUsersInWindow(course) {
  const users = new Set();
  for (const e of course.presses) users.add(e.userId);
  return users;
}

function computeMetrics(courseId) {
  const course = getCourse(courseId);
  const now = Date.now();
  pruneOld(course, now);
  const users = uniqueUsersInWindow(course);
  const uniqueCount = users.size;
  const roster = Math.max(1, Number(course.roster || 1));
  const pct = Math.round((uniqueCount / roster) * 100);
  return { uniqueCount, roster, pct, windowSec: WINDOW_MS / 1000 };
}

function maybeTrigger(courseId, io) {
  const course = getCourse(courseId);
  const now = Date.now();
  const { pct } = computeMetrics(courseId);
  if (pct >= THRESHOLD_PERCENT && now - course.lastTriggerAt >= COOLDOWN_MS) {
    course.lastTriggerAt = now;
    const confusedUsers = Array.from(uniqueUsersInWindow(course));
    // Create tutoring sessions for confused users (stub)
    for (const userId of confusedUsers) {
      const content = generateTutoringStub(courseId);
      course.tutoringByUser.set(userId, content);
      // Notify user sockets
      const sockets = state.socketsByUser.get(userId);
      if (sockets) {
        for (const sid of sockets) io.to(sid).emit('tutoring', content);
      }
    }
    // Notify instructors
    io.to(`course:${courseId}`).emit('trigger', {
      courseId,
      pct,
      threshold: THRESHOLD_PERCENT,
      at: new Date().toISOString(),
    });
  }
}

function recordPress(courseId, userId) {
  const course = getCourse(courseId);
  const now = Date.now();
  const last = course.lastPressByUser.get(userId) || 0;
  if (now - last < DEBOUNCE_MS) return false; // debounced
  course.lastPressByUser.set(userId, now);
  course.presses.push({ userId, ts: now });
  pruneOld(course, now);
  return true;
}

function generateTutoringStub(courseId) {
  // Placeholder tutoring content. Replace with LLM call.
  return {
    courseId,
    title: 'Quick Explanation',
    text: 'It looks like several students are confused. Here is a concise recap: Focus on key concept X, break it into steps A→B→C, and practice with the example below.',
    practice: [
      { q: 'Explain concept X in your own words.', a: 'Free response' },
      { q: 'Which step comes after B?', a: 'C' },
    ],
  };
}

// Routes
app.get('/health', (req, res) => {
  res.json({ ok: true, time: new Date().toISOString() });
});

app.get('/api/course/:id/metrics', (req, res) => {
  const { id } = req.params;
  res.json({ courseId: id, threshold: THRESHOLD_PERCENT, ...computeMetrics(id) });
});

app.get('/api/course/:id/roster', (req, res) => {
  const { id } = req.params;
  const course = getCourse(id);
  res.json({ courseId: id, roster: Number(course.roster || 0) });
});

app.post('/api/course/:id/roster', (req, res) => {
  const { id } = req.params;
  const { roster } = req.body || {};
  const course = getCourse(id);
  const value = Number(roster);
  if (!Number.isFinite(value) || value < 1) return res.status(400).json({ error: 'Invalid roster' });
  course.roster = value;
  res.json({ ok: true, courseId: id, roster: value });
});

app.get('/api/user/:userId/tutoring', (req, res) => {
  const { userId } = req.params;
  // Search across courses (dev simplification)
  let content = null;
  for (const [, course] of state.courses) {
    if (course.tutoringByUser.has(userId)) {
      content = course.tutoringByUser.get(userId);
      break;
    }
  }
  res.json({ userId, content });
});

const server = http.createServer(app);
const io = new Server(server, {
  cors: { origin: ALLOWED_ORIGIN, methods: ['GET', 'POST'] },
});

io.on('connection', (socket) => {
  const { role, courseId, userId, name } = socket.handshake.query;
  const course = getCourse(String(courseId || 'course-dev'));
  const cid = String(courseId || 'course-dev');
  const uid = userId ? String(userId) : `user-${socket.id}`;
  const r = role ? String(role) : 'student';
  const displayName = name ? String(name) : uid;

  // Join course room for instructor dashboards and student updates
  socket.join(`course:${cid}`);

  // Track sockets by user for tutoring push
  if (!state.socketsByUser.has(uid)) state.socketsByUser.set(uid, new Set());
  state.socketsByUser.get(uid).add(socket.id);

  // Send initial metrics
  socket.emit('metrics', { courseId: cid, threshold: THRESHOLD_PERCENT, ...computeMetrics(cid) });

  socket.on('confused', () => {
    const accepted = recordPress(cid, uid);
    const metrics = { courseId: cid, threshold: THRESHOLD_PERCENT, ...computeMetrics(cid) };
    io.to(`course:${cid}`).emit('metrics', metrics);
    if (accepted) maybeTrigger(cid, io);
  });

  socket.on('disconnect', () => {
    const set = state.socketsByUser.get(uid);
    if (set) {
      set.delete(socket.id);
      if (set.size === 0) state.socketsByUser.delete(uid);
    }
  });
});

server.listen(PORT, () => {
  /* eslint-disable no-console */
  console.log(`[blunote] server listening on :${PORT}`);
  console.log(`[blunote] CORS origin: ${ALLOWED_ORIGIN}`);
  console.log(`[blunote] Threshold: ${THRESHOLD_PERCENT}% | Window: ${WINDOW_MS / 1000}s | Debounce: ${DEBOUNCE_MS / 1000}s | Cooldown: ${COOLDOWN_MS / 1000}s`);
});

