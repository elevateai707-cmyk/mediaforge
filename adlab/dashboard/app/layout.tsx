import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "AdLab", description: "Review queue and results" };

const nav = [
  { href: "/", label: "Home" },
  { href: "/review", label: "Review" },
  { href: "/results", label: "Results" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b">
          <nav className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
            <span className="font-bold tracking-tight">AdLab</span>
            {nav.map((n) => (
              <Link key={n.href} href={n.href} className="text-sm text-muted-foreground hover:text-foreground">
                {n.label}
              </Link>
            ))}
            <span className="ml-auto text-xs text-muted-foreground">nothing uploads automatically</span>
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
