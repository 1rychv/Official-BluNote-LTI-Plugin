# SPEC-1-LTI Plugin

## Milestones

### Milestone 1: Core LTI 1.3 Launch
- Implement OIDC login + LTI ResourceLinkRequest validation.  
- Store user, course, and context in Postgres.  
- Verify launch works for both **instructor** and **student** roles in Moodle.  
- **Deliverable**: Instructor sees placeholder dashboard, student sees placeholder “Confused” button.  

### Milestone 2: Realtime Foundation
- Set up WebSocket service and Redis pub/sub.  
- Establish course/session channels.  
- Student client connects, sends “Confused” → Instructor dashboard shows live counter increment.  
- **Deliverable**: Working event loop from button press to dashboard update.  

### Milestone 3: Threshold Logic & Aggregation
- Implement rolling window counter with debounce and cooldown.  
- Integrate NRPS to fetch roster size for accurate denominator.  
- Instructor dashboard updates with % confusion and triggers threshold alert.  
- **Deliverable**: Instructor sees “Pause & Explain” alert when confusion threshold met.  

### Milestone 4: Tutoring Orchestration
- Build Tutoring Orchestrator to package context (lecture, slide/topic).  
- Call external LLM API, generate explanation + practice questions.  
- Store tutoring artifacts in S3-compatible store.  
- **Deliverable**: Confused students get a tutoring panel with AI-generated support content.  

### Milestone 5: Participation Credit (Optional)
- Implement AGS integration with Moodle.  
- Post back completion scores (e.g., pass/fail for tutoring session).  
- **Deliverable**: Moodle gradebook reflects participation credit.  

### Milestone 6: Analytics & Instructor Dashboard Enhancements
- Add real-time heatmaps and confusion timelines.  
- Store historical triggers/events in Postgres.  
- Add exportable reports for instructors.  
- **Deliverable**: Instructor can view confusion trends and export session summary.  

### Milestone 7: Pilot Deployment
- Deploy BluNote tool on a single Moodle course (pilot professor).  
- Collect logs, monitor latency and adoption.  
- Iterate based on student/lecturer feedback.  
- **Deliverable**: Successful live classroom pilot with usable dashboard + tutoring.  

### Milestone 8: Department Expansion
- Harden for multiple courses and instructors.  
- Add admin panel for institution-level management.  
- Implement retention & privacy controls per institution.  
- **Deliverable**: Multi-course readiness with secure, scalable deployment.  
