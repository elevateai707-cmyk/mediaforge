import Link from "next/link";

export function Footer() {
  return (
    <footer className="border-t border-bone/10">
      <div className="mx-auto grid max-w-[1440px] gap-10 px-4 py-14 md:grid-cols-4 md:px-8">
        <div>
          <p className="font-display text-4xl font-extrabold uppercase leading-none text-ember">
            Ignite
          </p>
          <p className="mt-4 max-w-[28ch] text-sm text-ash">
            A merch press for operator kits. Files, not vibes.
          </p>
        </div>
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-steel">
            Floor
          </p>
          <ul className="mt-3 space-y-2 font-mono text-xs uppercase tracking-[0.14em] text-ash">
            <li>
              <Link href="/shop">Drop</Link>
            </li>
            <li>
              <Link href="/cart">Ticket</Link>
            </li>
            <li>
              <Link href="/faq">FAQ</Link>
            </li>
            <li>
              <Link href="/accessibility">Accessibility</Link>
            </li>
          </ul>
        </div>
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-steel">
            Press
          </p>
          <ul className="mt-3 space-y-2 font-mono text-xs uppercase tracking-[0.14em] text-ash">
            <li>
              <Link href="/command">Command</Link>
            </li>
            <li>
              <Link href="/command/agents">Agents</Link>
            </li>
            <li>
              <Link href="/#research">Buying logic</Link>
            </li>
          </ul>
        </div>
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-steel">
            Colophon
          </p>
          <p className="mt-3 font-mono text-[11px] leading-relaxed text-ash">
            Big Shoulders Display, Atkinson Hyperlegible, Azeret Mono. Heat-press
            orange on warm soot. No invented KPIs.
          </p>
        </div>
      </div>
    </footer>
  );
}
