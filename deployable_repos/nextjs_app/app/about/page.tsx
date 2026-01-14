export default function AboutPage() {
  return (
    <div>
      <h1>About</h1>
      <p>This is a statically generated page.</p>
      <p>It was pre-rendered at build time and served as static HTML.</p>
      <p>
        This minimal Next.js application demonstrates key features for
        deployment testing: SSR, static generation, and API routes.
      </p>
    </div>
  );
}

export const dynamic = 'force-static';
