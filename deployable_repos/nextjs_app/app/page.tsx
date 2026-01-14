export default async function HomePage() {
  const appName = process.env.NEXT_PUBLIC_APP_NAME || 'Next.js App';
  const serverTime = new Date().toISOString();

  return (
    <div>
      <h1>{appName}</h1>
      <p>This page is server-side rendered on each request.</p>
      <p>Server time: <strong>{serverTime}</strong></p>
      <p>Refresh to see the timestamp update.</p>

      <h2>API Endpoints</h2>
      <ul>
        <li><a href="/api/hello">/api/hello</a> - Example API route</li>
        <li><a href="/api/health">/api/health</a> - Health check</li>
      </ul>
    </div>
  );
}

export const dynamic = 'force-dynamic';
