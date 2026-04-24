import * as React from "react";
import { cn } from "@/lib/utils";

interface AsciiDiagramProps extends React.HTMLAttributes<HTMLPreElement> {
  caption?: string;
}

/**
 * AsciiDiagram — frames a monospace art block with terminal-card styling.
 * Use for architecture sketches, attack-graph illustrations, etc.
 *
 * Pass box-drawing characters inside. No Shiki — intentional plain styling.
 */
export function AsciiDiagram({
  className,
  children,
  caption,
  ...props
}: AsciiDiagramProps) {
  return (
    <figure className="not-prose my-6">
      <pre
        className={cn(
          "overflow-x-auto rounded-md border border-[var(--color-border)] bg-[var(--color-card)]",
          "p-5 font-mono text-[13px] leading-[1.5] text-[var(--color-foreground)]",
          className,
        )}
        {...props}
      >
        {children}
      </pre>
      {caption && (
        <figcaption className="mt-2 pixel-label text-center text-[var(--color-muted-foreground)]">
          {caption}
        </figcaption>
      )}
    </figure>
  );
}
