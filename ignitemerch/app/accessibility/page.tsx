import type { Metadata } from "next";
import { absUrl } from "@/lib/seo";

export const metadata: Metadata = {
  title: "Accessibility",
  description:
    "IgniteMerch targets WCAG 2.2 Level AA in source code. How to report issues and what we do not use overlays for.",
  alternates: { canonical: absUrl("/accessibility") },
};

export default function AccessibilityPage() {
  return (
    <article className="mx-auto max-w-[760px] px-4 py-12 md:px-0">
      <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-ember">
        Statement
      </p>
      <h1 className="mt-3 font-display text-6xl font-extrabold uppercase leading-none">
        Accessibility
      </h1>
      <p className="mt-6 max-w-[65ch] leading-relaxed text-ash">
        This site is built to{" "}
        <a
          className="text-ember underline"
          href="https://www.w3.org/TR/WCAG22/"
        >
          WCAG 2.2 Level AA
        </a>{" "}
        in source code. We do not ship an accessibility overlay as a substitute.
        Automated axe checks catch regressions. Keyboard and screen-reader review
        still happens before a production launch.
      </p>
      <h2 className="mt-10 font-display text-3xl font-extrabold uppercase">
        What is in the baseline
      </h2>
      <ul className="mt-4 max-w-[65ch] list-disc space-y-2 pl-5 text-ash">
        <li>Skip link, landmarks, heading order, visible focus</li>
        <li>Keyboard access to nav, cart, checkout, and desk chat</li>
        <li>Form errors as text, not color alone</li>
        <li>Reduced motion, 44px primary targets, zoom allowed</li>
        <li>FAQ and product copy available without the chatbot</li>
      </ul>
      <h2 className="mt-10 font-display text-3xl font-extrabold uppercase">
        Report an issue
      </h2>
      <p className="mt-4 max-w-[65ch] text-ash">
        Email{" "}
        <a className="text-ember underline" href="mailto:access@localhost">
          access@localhost
        </a>{" "}
        with the page URL, what you were trying to do, and the browser or
        assistive tech. We log it and fix source, not a widget.
      </p>
      <p className="mt-6 font-mono text-[11px] uppercase tracking-[0.14em] text-steel">
        Title II ADA web rules for governments currently cite WCAG 2.1 AA. 2.2 AA
        includes those criteria and is the engineering target for every Elevate
        site going forward.
      </p>
    </article>
  );
}
