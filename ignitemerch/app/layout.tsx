import type { Metadata } from "next";
import {
  Azeret_Mono,
  Big_Shoulders,
  Atkinson_Hyperlegible,
} from "next/font/google";
import { Toaster } from "sonner";
import { SkipLink } from "@/components/a11y/skip-link";
import { DeskChat } from "@/components/chat/desk-chat";
import { SiteJsonLd } from "@/components/seo/site-json-ld";
import { Footer } from "@/components/storefront/footer";
import { Navigation } from "@/components/storefront/navigation";
import { SITE_NAME, SITE_URL, absUrl } from "@/lib/seo";
import "./globals.css";

const display = Big_Shoulders({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["500", "700", "800"],
});

const body = Atkinson_Hyperlegible({
  variable: "--font-body",
  subsets: ["latin"],
  weight: ["400", "700"],
});

const mono = Azeret_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "600"],
});

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: SITE_NAME,
    template: `%s · ${SITE_NAME}`,
  },
  description:
    "Press-ready operator kits: ComfyUI photo desks, UGC ad floors, n8n lines, and Cursor skills. Priced to sell, built to run tonight.",
  alternates: { canonical: absUrl("/") },
  openGraph: {
    title: SITE_NAME,
    description:
      "Operator kits you import tonight. ComfyUI, UGC, n8n, Cursor.",
    url: absUrl("/"),
    siteName: SITE_NAME,
    type: "website",
    locale: "en_US",
  },
  twitter: {
    card: "summary_large_image",
    title: SITE_NAME,
    description:
      "Operator kits you import tonight. ComfyUI, UGC, n8n, Cursor.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${display.variable} ${body.variable} ${mono.variable} heat-wash min-h-screen antialiased`}
      >
        <SiteJsonLd />
        <SkipLink />
        <div className="press-grain pointer-events-none fixed inset-0 z-0 opacity-[0.07] mix-blend-overlay" />
        <div className="relative z-10">
          <Navigation />
          <div id="main" tabIndex={-1}>
            {children}
          </div>
          <Footer />
        </div>
        <DeskChat />
        <Toaster
          theme="dark"
          position="bottom-right"
          toastOptions={{
            style: {
              background: "oklch(0.21 0.032 46)",
              border: "1px solid oklch(0.93 0.022 88 / 0.12)",
              color: "oklch(0.93 0.022 88)",
              borderRadius: "2px",
            },
          }}
        />
      </body>
    </html>
  );
}
