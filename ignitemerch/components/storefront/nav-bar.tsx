"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

interface NavBarProps {
  cartCount: number;
}

export function NavBar({ cartCount }: NavBarProps) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const onCommand = pathname.startsWith("/command");

  return (
    <header className="sticky top-0 z-40 border-b border-bone/10 bg-soot/92 backdrop-blur-sm">
      <div className="mx-auto flex max-w-[1440px] items-center justify-between gap-4 px-4 py-3 md:px-8">
        <Link href="/" className="flex items-baseline gap-3">
          <span className="font-display text-3xl font-extrabold uppercase leading-none tracking-tight text-ember">
            Ignite
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-ash">
            {onCommand ? "Command" : "Merch press"}
          </span>
        </Link>

        <nav className="hidden items-center gap-7 md:flex" aria-label="Primary">
          {(onCommand ? commandLinks : floorLinks).map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="font-mono text-[11px] uppercase tracking-[0.16em] text-ash transition-colors duration-200 hover:text-bone"
            >
              {link.label}
            </Link>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <Link
            href={onCommand ? "/" : "/command"}
            className="hidden font-mono text-[11px] uppercase tracking-[0.16em] text-ash hover:text-bone md:inline"
          >
            {onCommand ? "Floor" : "Command"}
          </Link>
          <Link
            href="/cart"
            className="inline-flex h-10 items-center bg-bone px-3 font-mono text-[11px] uppercase tracking-[0.16em] text-soot transition-transform duration-200 ease-expo active:scale-[0.96]"
          >
            Ticket {cartCount}
            <span className="sr-only"> items</span>
          </Link>
          <button
            type="button"
            className="inline-flex h-11 min-w-11 items-center justify-center border border-bone/15 font-mono text-[10px] uppercase tracking-[0.14em] text-bone md:hidden"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-label="Menu"
          >
            {open ? "Close" : "Menu"}
          </button>
        </div>
      </div>
      {open ? (
        <div className="border-t border-bone/10 px-4 py-4 md:hidden">
          <div className="flex flex-col gap-3">
            {(onCommand ? commandLinks : floorLinks).map((link) => (
              <Link
                key={link.href}
                href={link.href}
                onClick={() => setOpen(false)}
                className="font-mono text-xs uppercase tracking-[0.16em] text-bone"
              >
                {link.label}
              </Link>
            ))}
          </div>
        </div>
      ) : null}
    </header>
  );
}

const floorLinks = [
  { href: "/shop", label: "Drop" },
  { href: "/faq", label: "FAQ" },
  { href: "/shop?type=WORKFLOW", label: "Workflows" },
  { href: "/shop?type=CREATOR_KIT", label: "Creator" },
  { href: "/#research", label: "Why these SKUs" },
];

const commandLinks = [
  { href: "/command", label: "Board" },
  { href: "/command/agents", label: "Agents" },
  { href: "/command/campaigns", label: "Campaigns" },
  { href: "/command/intelligence", label: "Intelligence" },
];
