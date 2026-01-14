import { NextResponse } from 'next/server';

export async function GET() {
  const appName = process.env.NEXT_PUBLIC_APP_NAME || 'Next.js App';

  return NextResponse.json({
    message: `Hello from ${appName}!`,
    timestamp: new Date().toISOString(),
  });
}
