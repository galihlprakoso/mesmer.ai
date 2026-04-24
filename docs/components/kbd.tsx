import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Kbd — keyboard-shortcut pill. Use in CLI docs and command palette hints.
 * Example: <Kbd>⌘</Kbd> <Kbd>K</Kbd>
 */
export function Kbd({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex min-w-[1.5em] items-center justify-center gap-1 rounded border border-[var(--color-border)]",
        "bg-[var(--color-muted)] px-1.5 py-0.5 font-mono text-[0.75em] font-medium",
        "text-[var(--color-foreground)] shadow-[inset_0_-1px_0_var(--color-border)]",
        "align-middle",
        className,
      )}
      {...props}
    >
      {children}
    </kbd>
  );
}
