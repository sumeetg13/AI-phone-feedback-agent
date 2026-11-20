# feedback-frontend

Next.js frontend for DotAI Labs AI Certificate course feedback.

- `/` — the feedback form learners fill in
- `/admin` — asks for the backend's admin token, then shows two tabs:
  - **Results**: summary, per-course table, every response
  - **Phone calls**: upload a CSV of people (`name,email,course,phoneNum`), start the AI feedback
    calls, and follow each person's status, form progress and call transcript

## Run

Start `../feedback-service` first (port 8010), then:

```bash
npm install
npm run dev
```

Open http://localhost:3000.

## Configuration

Requests to `/api/*` are proxied to the backend (see `next.config.ts`), so the browser never
calls it directly. Point the proxy elsewhere with `FEEDBACK_API_URL` (default
`http://localhost:8010`), e.g. in `.env.local`. It is read when `next dev` starts or at
`next build` time — changing it for a production build means rebuilding.
