# Admin Dashboard

A React-based admin dashboard for user management and analytics. Built with Vite, React 18, TypeScript, and Express.

## Features

- Dashboard with statistics and charts
- User management (list and detail views)
- RESTful API backend
- Health check endpoint for deployment monitoring

## Tech Stack

- **Frontend:** React 18, TypeScript, Vite, React Router
- **Backend:** Node.js 20, Express
- **Styling:** CSS (no external dependencies)

## Prerequisites

- Node.js 20 or higher
- npm

## Local Development

1. Install dependencies:

```bash
npm install
```

2. Copy the environment file:

```bash
cp .env.example .env
```

3. Start the development servers:

```bash
npm run dev
```

This runs both the Vite dev server (port 5173) and the Express API server (port 3000) concurrently.

- Frontend: http://localhost:5173
- API: http://localhost:3000/api

## Production Build

1. Build the frontend:

```bash
npm run build
```

2. Start the production server:

```bash
npm start
```

The server runs on port 3000 and serves both the API and the built React app.

- App: http://localhost:3000
- Health check: http://localhost:3000/health

## API Endpoints

- `GET /api/users` - List all users
- `GET /api/users/:id` - Get user by ID
- `GET /api/stats` - Dashboard statistics
- `GET /health` - Health check

## Environment Variables

- `PORT` - Server port (default: 3000)
- `API_URL` - API URL for frontend (development only)

## Project Structure

```
admin_dashboard/
├── server/
│   └── index.js          # Express backend
├── src/
│   ├── components/
│   │   ├── Dashboard.tsx # Stats and charts
│   │   ├── UserList.tsx  # User table
│   │   └── UserDetail.tsx# User profile
│   ├── App.tsx           # Main app with routing
│   ├── App.css           # Styles
│   └── main.tsx          # Entry point
├── index.html
├── vite.config.ts
├── tsconfig.json
└── package.json
```
