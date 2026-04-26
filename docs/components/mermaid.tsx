"use client";

import { useEffect, useId, useRef, useState } from "react";

interface MermaidProps {
  chart: string;
  caption?: string;
}

const themeVariables = {
  // Base palette — phosphor on near-black, kept in lockstep with global.css.
  background: "#141A17",
  primaryColor: "#141A17",
  primaryTextColor: "#E6EDE8",
  primaryBorderColor: "#00D67A",
  secondaryColor: "#1B2520",
  secondaryTextColor: "#E6EDE8",
  secondaryBorderColor: "rgba(140, 155, 145, 0.4)",
  tertiaryColor: "#0A0E0C",
  tertiaryTextColor: "#E6EDE8",
  tertiaryBorderColor: "rgba(140, 155, 145, 0.3)",
  lineColor: "rgba(140, 155, 145, 0.55)",
  textColor: "#E6EDE8",
  mainBkg: "#141A17",
  nodeBorder: "#00D67A",
  clusterBkg: "#0F1411",
  clusterBorder: "rgba(140, 155, 145, 0.3)",
  defaultLinkColor: "rgba(140, 155, 145, 0.6)",
  edgeLabelBackground: "#0A0E0C",
  // Sequence diagrams
  actorBkg: "#141A17",
  actorBorder: "#00D67A",
  actorTextColor: "#E6EDE8",
  actorLineColor: "rgba(140, 155, 145, 0.6)",
  signalColor: "#E6EDE8",
  signalTextColor: "#E6EDE8",
  labelBoxBkgColor: "#1B2520",
  labelBoxBorderColor: "rgba(140, 155, 145, 0.4)",
  labelTextColor: "#E6EDE8",
  loopTextColor: "#E6EDE8",
  noteBkgColor: "#1B2520",
  noteTextColor: "#E6EDE8",
  noteBorderColor: "rgba(140, 155, 145, 0.4)",
  // State diagrams
  fillType0: "#141A17",
  fillType1: "#1B2520",
  fillType2: "#0F1411",
};

export function Mermaid({ chart, caption }: MermaidProps) {
  const id = useId().replace(/:/g, "");
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "base",
          fontFamily:
            'var(--font-mono), ui-monospace, "JetBrains Mono", monospace',
          themeVariables,
          flowchart: { curve: "basis", padding: 16 },
          sequence: { mirrorActors: false },
          securityLevel: "loose",
        });

        const renderId = `mermaid-${id}`;
        const { svg } = await mermaid.render(renderId, chart.trim());
        if (cancelled || !containerRef.current) return;
        containerRef.current.innerHTML = svg;
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chart, id]);

  return (
    <figure className="my-6 overflow-hidden rounded-lg border border-[var(--color-fd-border)] bg-[var(--color-fd-card)]">
      <div className="overflow-x-auto p-6">
        {error ? (
          <pre className="text-sm text-[var(--color-fd-muted-foreground)]">
            mermaid render error: {error}
          </pre>
        ) : (
          <div
            ref={containerRef}
            className="mx-auto flex justify-center [&_svg]:max-w-full [&_svg]:h-auto"
          />
        )}
      </div>
      {caption && (
        <figcaption className="pixel-label border-t border-[var(--color-fd-border)] bg-[var(--color-fd-background)]/60 px-6 py-2 text-center text-[var(--color-fd-muted-foreground)]">
          {caption}
        </figcaption>
      )}
    </figure>
  );
}
