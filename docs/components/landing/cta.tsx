import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function Cta() {
  return (
    <section className="mx-auto w-full max-w-4xl px-4 py-24">
      <div className="relative overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-10 text-center md:p-16">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_40%_at_50%_100%,hsla(155,100%,42%,0.12),transparent)]" />
        <span className="pixel-label relative text-[var(--color-primary)]">
          ▸ ready.to.probe
        </span>
        <h2 className="relative mt-4 font-mono text-3xl font-bold tracking-tight sm:text-4xl">
          Red-team a target in under 5 minutes.
        </h2>
        <p className="relative mx-auto mt-4 max-w-lg text-[var(--color-muted-foreground)]">
          Point mesmer at an OpenAI-compatible endpoint, a REST API, or a
          WebSocket target. Write a scenario YAML. Run. Watch the graph.
        </p>
        <div className="relative mt-8 flex flex-wrap items-center justify-center gap-3">
          <Link
            href="/docs/getting-started/install"
            className={cn(buttonVariants({ size: "lg" }))}
          >
            Install mesmer
            <ArrowRight className="size-4" />
          </Link>
          <Link
            href="/docs/concepts/attack-graph"
            className={cn(buttonVariants({ variant: "outline", size: "lg" }))}
          >
            Read the concepts
          </Link>
        </div>
      </div>
    </section>
  );
}
