interface ResearchStripProps {
  notes: Array<{ lotNumber: string; name: string; researchNote: string }>;
}

export function ResearchStrip({ notes }: ResearchStripProps) {
  return (
    <section id="research" className="border-y border-bone/10 bg-ink">
      <div className="mx-auto max-w-[1440px] px-4 py-16 md:px-8">
        <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-ember">
          Buying logic
        </p>
        <h2 className="mt-3 max-w-[18ch] font-display text-5xl font-extrabold uppercase leading-[0.88] tracking-tight md:text-7xl">
          We stock what the market already pays for.
        </h2>
        <p className="mt-5 max-w-[62ch] text-ash">
          Catalog choices follow live demand, not a fake dashboard. Generic
          ChatGPT prompt piles sit in a $10–$19 revenue dead zone. Specific
          desks (n8n lines, ComfyUI photo graphs, job-named prompt packs) sit
          in the $29–$79 band where people actually check out.
        </p>
        <ol className="mt-12 grid gap-px bg-bone/10 md:grid-cols-2">
          {notes.map((note) => (
            <li key={note.lotNumber} className="bg-soot p-6">
              <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-ember">
                Lot {note.lotNumber}
              </p>
              <h3 className="mt-2 font-display text-2xl font-extrabold uppercase leading-none tracking-tight">
                {note.name}
              </h3>
              <p className="mt-4 max-w-[48ch] text-sm leading-relaxed text-ash">
                {note.researchNote}
              </p>
            </li>
          ))}
        </ol>
        <p className="mt-8 max-w-[70ch] font-mono text-[11px] leading-relaxed text-steel">
          Sources used while stocking: Gumroad prompt-market analysis on DEV,
          2026 digital product roundups from iImagined and DataVook, and
          ecommerce photography cost writeups from Apatero, Enhancecraft, and
          Nightjar. No store metrics are invented for this page.
        </p>
      </div>
    </section>
  );
}
