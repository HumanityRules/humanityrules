# Next.js Demo App

A minimal Next.js 14 application with App Router, demonstrating SSR, static generation, and API routes.

## Requirements

- Node.js 20+
- npm

## Local Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. Copy environment variables:
   ```bash
   cp .env.example .env.local
   ```

3. Run development server:
   ```bash
   npm run dev
   ```

4. Open http://localhost:3000

## Production Build

```bash
npm run build
npm start
```

The production server runs on port 3000 by default.

## Environment Variables

- **NEXT_PUBLIC_APP_NAME** - Application name displayed in the UI
- **PORT** - Server port (default: 3000)

## Endpoints

- **/** - Home page (server-side rendered)
- **/about** - About page (statically generated)
- **/api/hello** - Example API route
- **/api/health** - Health check endpoint

## Port

The application listens on port **3000**.
