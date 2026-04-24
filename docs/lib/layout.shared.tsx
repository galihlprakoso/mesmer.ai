import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";
import { Terminal } from "lucide-react";

/**
 * Shared layout props — re-used by both HomeLayout and DocsLayout.
 * Owns the nav title, logo, and GitHub link so they can't drift.
 */
export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <>
          <Terminal
            className="size-5 text-[var(--color-fd-primary)]"
            strokeWidth={2.25}
          />
          <span className="font-mono font-semibold tracking-tight">
            mesmer
          </span>
          <span className="pixel-label text-[var(--color-fd-muted-foreground)] ml-1">
            v0.1
          </span>
        </>
      ),
      transparentMode: "top",
    },
    githubUrl: "https://github.com/galihlprakoso/mesmer",
    links: [
      {
        text: "Docs",
        url: "/docs",
        active: "nested-url",
      },
      {
        text: "Modules",
        url: "/docs/modules",
        active: "nested-url",
      },
      {
        text: "llms.txt",
        url: "/llms-full.txt",
        external: true,
      },
    ],
  };
}
