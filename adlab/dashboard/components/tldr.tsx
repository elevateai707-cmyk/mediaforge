export function TLDR({ lines }: { lines: string[] }) {
  return (
    <div className="mb-6 rounded-lg bg-secondary px-5 py-4">
      <p className="mb-1 text-xs font-bold uppercase tracking-wider text-muted-foreground">TL;DR</p>
      <ul className="space-y-1 text-sm">
        {lines.slice(0, 3).map((l) => (
          <li key={l}>• {l}</li>
        ))}
      </ul>
    </div>
  );
}

export function Loading({ error }: { error?: string }) {
  return (
    <p className="text-sm text-muted-foreground">
      {error ? `Can't reach AdLab: ${error}. Is \`adlab serve\` running?` : "Loading…"}
    </p>
  );
}
