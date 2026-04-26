import { AttackGraphDemo } from "@/components/landing/attack-graph-demo";

export function GraphDemo() {
  return (
    <section className="mx-auto w-full max-w-6xl px-4 py-20">
      <div className="mb-10 flex flex-col items-center text-center">
        <span className="pixel-label mb-3 text-[var(--color-muted-foreground)]">
          ▸ what.you.see.after.a.run
        </span>
        <h2 className="font-mono text-3xl font-bold tracking-tight sm:text-4xl">
          Every attempt is a node. Every win is replayable.
        </h2>
        <p className="mt-3 max-w-2xl text-[var(--color-muted-foreground)]">
          Mesmer writes a per-trial decision tree to{" "}
          <code className="rounded bg-[var(--color-fd-card)] px-1.5 py-0.5 font-mono text-[0.85em]">
            ~/.mesmer/
          </code>{" "}
          and an interactive HTML mind-map next to every benchmark cell. Hover
          a node to see the delegate prompt; click the leader-verdict square to
          read the concluded text. The whole tree opens offline.
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
