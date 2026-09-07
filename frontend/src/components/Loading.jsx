export default function Loading({ label = 'Loading…', testId = 'loading' }) {
  return (
    <div data-testid={testId} style={{ textAlign: 'center', padding: 48, color: 'var(--muted)' }}>
      <div className="sz-spinner" aria-hidden />
      <p>{label}</p>
    </div>
  )
}
