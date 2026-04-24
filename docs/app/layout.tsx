import "./global.css";
import type { ReactNode } from "react";
import type { Metadata } from "next";
import { RootProvider } from "fumadocs-ui/provider";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Inter, JetBrains_Mono } from "next/font/google";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://mesmer.vercel.app"),
  title: {
    default: "mesmer — cognitive hacking toolkit for LLMs",
    template: "%s — mesmer",
  },
  description:
    "Treat AI as minds to hack, not software to fuzz. Mesmer is a cognitive hacking toolkit for LLMs — multi-turn red-teaming with a persistent, self-improving attack graph.",
  keywords: [
    "llm",
    "ai safety",
    "red team",
    "pentest",
    "jailbreak",
    "prompt injection",
    "security",
    "cognitive attack",
  ],
  openGraph: {
    title: "mesmer — cognitive hacking toolkit for LLMs",
    description:
      "Multi-turn LLM red-teaming with a persistent, self-improving attack graph.",
    type: "website",
    url: "https://mesmer.vercel.app",
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${GeistSans.variable} ${GeistMono.variable} ${inter.variable} ${jetbrainsMono.variable}`}
      style={{
        // Wire Geist into the font vars our global.css expects.
        ["--font-geist" as string]: GeistSans.style.fontFamily,
      }}
    >
      <body>
        <RootProvider
          theme={{
            defaultTheme: "dark",
            forcedTheme: "dark",
            enableSystem: false,
          }}
        >
          {children}
        </RootProvider>
      </body>
    </html>
  );
}
