import Link from "next/link";
import { Github, ExternalLink, Terminal as TerminalIcon } from "lucide-react";

const projectLinks = [
  { label: "Quickstart", href: "/docs/getting-started/quickstart" },
  { label: "Tutorials", href: "/docs/tutorials/your-first-attack" },
  { label: "Modules", href: "/docs/modules" },
  { label: "Benchmarks", href: "/docs/benchmarks" },
];

const resourceLinks = [
  { label: "CLI reference", href: "/docs/cli" },
  { label: "Recipes", href: "/docs/recipes/throttling" },
  { label: "Techniques", href: "/docs/techniques/foot-in-door" },
  { label: "llms.txt", href: "/llms-full.txt", external: true },
];

const externalLinks = [
  {
    label: "GitHub",
    href: "https://github.com/galihlprakoso/mesmer.ai",
    icon: Github,
  },
  {
    label: "PyPI",
    href: "https://pypi.org/project/mesmer/",
    icon: ExternalLink,
  },
  {
    label: "Issues",
    href: "https://github.com/galihlprakoso/mesmer.ai/issues",
    icon: ExternalLink,
  },
];

export function Footer() {
  const year = new Date().getFullYear();
  return (
    <footer className="mt-16 border-t border-[var(--color-fd-border)] bg-[var(--color-fd-card)]/40">
      <div className="mx-auto w-full max-w-6xl px-4 py-12">
        <div className="grid gap-10 md:grid-cols-4">
          {/* Brand column */}
          <div>
            <div className="flex items-center gap-2">
              <TerminalIcon
                className="size-5 text-[var(--color-fd-primary)]"
                strokeWidth={2.25}
              />
              <span className="font-mono font-semibold tracking-tight">
                mesmer
              </span>
              <span className="pixel-label text-[var(--color-fd-muted-foreground)]">
                v0.1
              </span>
            </div>
            <p className="mt-3 max-w-[14rem] text-sm leading-relaxed text-[var(--color-fd-muted-foreground)]">
              Cognitive hacking toolkit for LLMs. Multi-turn red-teaming with a
              persistent attack graph.
            </p>
            <span className="pixel-label mt-4 inline-block rounded border border-[var(--color-fd-border)] px-2 py-1 text-[var(--color-fd-muted-foreground)]">
              MIT licensed
            </span>
          </div>

          {/* Project column */}
          <div>
            <h3 className="pixel-label text-[var(--color-fd-primary)]">
              ▸ project
            </h3>
            <ul className="mt-4 space-y-2.5 text-sm">
              {projectLinks.map((l) => (
                <li key={l.href}>
                  <Link
                    href={l.href}
                    className="text-[var(--color-fd-muted-foreground)] transition-colors hover:text-[var(--color-fd-foreground)]"
                  >
                    {l.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>

          {/* Resources column */}
          <div>
            <h3 className="pixel-label text-[var(--color-fd-primary)]">
              ▸ resources
            </h3>
            <ul className="mt-4 space-y-2.5 text-sm">
              {resourceLinks.map((l) =>
                l.external ? (
                  <li key={l.href}>
                    <a
                      href={l.href}
                      className="inline-flex items-center gap-1.5 text-[var(--color-fd-muted-foreground)] transition-colors hover:text-[var(--color-fd-foreground)]"
                    >
                      {l.label}
                      <ExternalLink className="size-3" strokeWidth={2} />
                    </a>
                  </li>
                ) : (
                  <li key={l.href}>
                    <Link
                      href={l.href}
                      className="text-[var(--color-fd-muted-foreground)] transition-colors hover:text-[var(--color-fd-foreground)]"
                    >
                      {l.label}
                    </Link>
                  </li>
                ),
              )}
            </ul>
          </div>

          {/* External column */}
          <div>
            <h3 className="pixel-label text-[var(--color-fd-primary)]">
              ▸ elsewhere
            </h3>
            <ul className="mt-4 space-y-2.5 text-sm">
              {externalLinks.map((l) => {
                const Icon = l.icon;
                return (
                  <li key={l.href}>
                    <a
                      href={l.href}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-2 text-[var(--color-fd-muted-foreground)] transition-colors hover:text-[var(--color-fd-foreground)]"
                    >
                      <Icon className="size-3.5" strokeWidth={2} />
                      {l.label}
                    </a>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>

        {/* Bottom rule */}
        <div className="mt-10 flex flex-col items-start justify-between gap-3 border-t border-[var(--color-fd-border)] pt-6 text-xs sm:flex-row sm:items-center">
          <span className="pixel-label text-[var(--color-fd-muted-foreground)]">
            mesmer · MIT · built with LiteLLM · {year}
          </span>
          <span className="pixel-label inline-flex items-center gap-1.5 text-[var(--color-fd-muted-foreground)]">
            <span>~/.mesmer</span>
            <span
              aria-hidden
              className="inline-block h-[0.85em] w-[0.5em] animate-pulse bg-[var(--color-fd-primary)]"
            />
          </span>
        </div>
      </div>
    </footer>
  );
}
