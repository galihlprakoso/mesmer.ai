import Link from "next/link";
import { ArrowRight, GitBranch, Layers3 } from "lucide-react";
import { MODULES } from "@/components/module-grid";
import { Badge } from "@/components/ui/badge";

const leaders = MODULES.filter((mod) => mod.category === "attack");
const techniqueGroups = [
  {
    label: "Recon",
    description: "Profile the target and turn evidence into ordered plans.",
    modules: MODULES.filter(
      (mod) => mod.category === "profiler" || mod.category === "planner",
    ),
  },
  {
    label: "Field",
    description: "Payload shape, role boundaries, tool surfaces.",
    modules: MODULES.filter((mod) => mod.category === "field"),
  },
  {
    label: "Cognitive",
    description: "Bias and persuasion patterns for guarded targets.",
    modules: MODULES.filter(
      (mod) =>
        mod.category === "cognitive-bias" || mod.category === "psychological",
    ),
  },
  {
    label: "Linguistic",
    description: "Translation, narrative, and pragmatic reframing.",
    modules: MODULES.filter((mod) => mod.category === "linguistic"),
  },
];

function moduleHref(mod: (typeof MODULES)[number]) {
  if (mod.category === "attack") return `/docs/modules/${mod.slug}`;
  if (mod.category === "profiler" || mod.category === "planner") return null;
  return `/docs/techniques/${mod.slug}`;
}

export function ModuleShowcase() {
  return (
    <section className="mx-auto w-full max-w-6xl px-4 py-20">
      <div className="mb-10 flex flex-col items-center text-center">
        <span className="pixel-label mb-3 text-[var(--color-muted-foreground)]">
          ▸ modules.shipped
        </span>
        <h2 className="font-mono text-3xl font-bold tracking-tight sm:text-4xl">
          Twenty-one modules without the wall of cards.
        </h2>
        <p className="mt-3 max-w-xl text-[var(--color-muted-foreground)]">
          Leaders, profilers, planners, and technique families compile into one
          attack graph. The landing view stays compact; the docs keep the full
          catalog.
        </p>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1.05fr_1fr]">
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]/65 p-5">
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <div className="pixel-label text-[var(--color-primary)]">
                leader graph
              </div>
              <h3 className="mt-2 font-mono text-xl font-semibold">
                Four orchestrators choose the path.
              </h3>
            </div>
            <GitBranch className="size-5 shrink-0 text-[var(--color-primary)]" />
          </div>

          <div className="space-y-3">
            {leaders.map((mod, index) => {
              const Icon = mod.icon;
              const href = moduleHref(mod);
              const content = (
                <div className="group grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-md border border-[var(--color-border)] bg-[var(--color-background)]/35 p-3 transition-colors hover:border-[var(--color-primary)]/50">
                  <div className="flex size-9 items-center justify-center rounded border border-[var(--color-primary)]/35 bg-[var(--color-primary)]/10">
                    <Icon className="size-4 text-[var(--color-primary)]" />
                  </div>
                  <div className="min-w-0">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="pixel-label shrink-0 text-[var(--color-muted-foreground)]">
                        #{index + 1}
                      </span>
                      <span className="truncate font-mono text-sm font-semibold">
                        {mod.name}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-[var(--color-muted-foreground)]">
                      {mod.description}
                    </p>
                  </div>
                  <ArrowRight className="size-4 text-[var(--color-muted-foreground)] transition-colors group-hover:text-[var(--color-primary)]" />
                </div>
              );

              return href ? (
                <Link key={mod.slug} href={href} className="block no-underline">
                  {content}
                </Link>
              ) : (
                <div key={mod.slug}>{content}</div>
              );
            })}
          </div>
        </div>

        <div className="grid gap-5">
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]/65 p-5">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="pixel-label text-[var(--color-primary)]">
                  technique families
                </div>
                <h3 className="mt-2 font-mono text-xl font-semibold">
                  Dense by default, explorable on demand.
                </h3>
              </div>
              <Layers3 className="size-5 shrink-0 text-[var(--color-primary)]" />
            </div>

            <div className="space-y-4">
              {techniqueGroups.map((group) => (
                <div key={group.label}>
                  <div className="mb-2 flex items-end justify-between gap-3">
                    <div>
                      <div className="font-mono text-sm font-semibold">
                        {group.label}
                      </div>
                      <p className="text-xs text-[var(--color-muted-foreground)]">
                        {group.description}
                      </p>
                    </div>
                    <Badge variant="outline" className="shrink-0 text-[0.625rem]">
                      {group.modules.length}
                    </Badge>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {group.modules.map((mod) => {
                      const Icon = mod.icon;
                      const href = moduleHref(mod);
                      const chip = (
                        <span className="inline-flex max-w-full items-center gap-1.5 rounded border border-[var(--color-border)] bg-[var(--color-background)]/40 px-2 py-1 font-mono text-[11px] text-[var(--color-foreground)] transition-colors hover:border-[var(--color-primary)]/50">
                          <Icon className="size-3 shrink-0 text-[var(--color-primary)]" />
                          <span className="truncate">{mod.name}</span>
                        </span>
                      );

                      return href ? (
                        <Link
                          key={mod.slug}
                          href={href}
                          className="max-w-full no-underline"
                        >
                          {chip}
                        </Link>
                      ) : (
                        <span key={mod.slug}>{chip}</span>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <Stat value="21" label="modules" />
            <Stat value="4" label="delegate groups" />
            <Stat value="1" label="memory graph" />
          </div>
        </div>
      </div>
    </section>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]/65 p-4 text-center">
      <div className="font-mono text-2xl font-semibold text-[var(--color-primary)]">
        {value}
      </div>
      <div className="pixel-label mt-1 text-[var(--color-muted-foreground)]">
        {label}
      </div>
    </div>
  );
}
