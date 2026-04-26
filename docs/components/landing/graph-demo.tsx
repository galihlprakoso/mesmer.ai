import { AttackGraphDemo } from "@/components/landing/attack-graph-demo";

export function GraphDemo() {
  return (
    <section className="mx-auto w-full max-w-6xl px-4 py-20">
      <div className="mb-10 flex flex-col items-center text-center">
        <span className="pixel-label mb-3 text-[var(--color-muted-foreground)]">
          ▸ what.you.see.after.a.run
        </span>
        <h2 className="font-mono text-3xl font-bold tracking-tight sm:text-4xl">
          The app view, compressed into a landing-page moment.
        </h2>
        <p className="mt-3 max-w-2xl text-[var(--color-muted-foreground)]">
          Mesmer writes the attack graph, node inspector, and belief map to{" "}
          <code className="rounded bg-[var(--color-fd-card)] px-1.5 py-0.5 font-mono text-[0.85em]">
            ~/.mesmer/
          </code>{" "}
          next to every benchmark cell. The landing page now shows the same
          working loop: plan, branch, judge, remember, and replay.
        </p>
      </div>

      <AttackGraphDemo />

      <div className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs">
        <span className="pixel-label text-[var(--color-muted-foreground)]">
          → live demo on every <code className="font-mono">mesmer bench</code>{" "}
          run
        </span>
        <span className="pixel-label text-[var(--color-muted-foreground)]">
          → backfill old runs with <code className="font-mono">mesmer bench-viz</code>
        </span>
      </div>
    </section>
  );
}
