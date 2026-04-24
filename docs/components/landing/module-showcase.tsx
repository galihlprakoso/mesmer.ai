import { ModuleGrid } from "@/components/module-grid";

export function ModuleShowcase() {
  return (
    <section className="mx-auto w-full max-w-6xl px-4 py-20">
      <div className="mb-10 flex flex-col items-center text-center">
        <span className="pixel-label mb-3 text-[var(--color-muted-foreground)]">
          ▸ modules.shipped
        </span>
        <h2 className="font-mono text-3xl font-bold tracking-tight sm:text-4xl">
          Eight attacks, one graph.
        </h2>
        <p className="mt-3 max-w-xl text-[var(--color-muted-foreground)]">
          Every module is a ReAct agent with its own system prompt and judge
          rubric. The leader composes them, the graph remembers what worked.
        </p>
      </div>
      <ModuleGrid />
    </section>
  );
}
