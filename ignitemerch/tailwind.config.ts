import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        soot: "var(--soot)",
        ink: "var(--ink)",
        press: "var(--press)",
        ember: "var(--ember)",
        "ember-hot": "var(--ember-hot)",
        bone: "var(--bone)",
        ash: "var(--ash)",
        steel: "var(--steel)",
        rust: "var(--rust)",
      },
      fontFamily: {
        display: ["var(--font-display)", "sans-serif"],
        body: ["var(--font-body)", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      letterSpacing: {
        lot: "0.18em",
      },
      boxShadow: {
        heat: "0 24px 60px -28px oklch(0.55 0.18 48 / 0.55)",
        ticket: "0 1px 0 oklch(0.94 0.02 88 / 0.08)",
      },
      transitionTimingFunction: {
        expo: "cubic-bezier(0.16, 1, 0.3, 1)",
      },
    },
  },
  plugins: [],
};

export default config;
