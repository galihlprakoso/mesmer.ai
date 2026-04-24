import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * TerminalPre — replaces the default Fumadocs <pre> for every fenced code
 * block in MDX. Wraps code in fake terminal chrome + keeps Shiki highlighting.
 *
 * The dollar-prompt prefix is added ONLY when the code block language is
 * `bash`, `sh`, `shell`, or `console`. For other languages (ts, yaml, python)
 * we keep the chrome but skip the prompt — a `$` on a YAML file reads wrong.
 */

interface TerminalPreProps extends React.HTMLAttributes<HTMLPreElement> {
  "data-language"?: string;
  title?: string;
}

const SHELL_LANGS = new Set([
  "bash",
  "sh",
  "shell",
  "zsh",
  "console",
]);

export function TerminalPre({
  className,
  title,
  children,
  ...props
}: TerminalPreProps) {
  // Fumadocs' default <pre> receives data-language from rehype-pretty-code.
  const lang = props["data-language"];
  const isShell = lang ? SHELL_LANGS.has(lang) : false;
  const label = title ?? (isShell ? "zsh" : (lang ?? "code"));

  return (
    <div className="not-prose my-5 overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm">
      {/* Terminal chrome bar */}
      <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-2">
        <span
          className={cn(
            "size-2 rounded-full",
            isShell
              ? "bg-[var(--color-primary)] shadow-[0_0_6px_var(--color-primary)]"
              : "bg-[var(--color-muted-foreground)]/40",
          )}
        />
        <span className="pixel-label text-[var(--color-muted-foreground)]">
          {label}
        </span>
      </div>
      {/* Code body — keep Fumadocs' pre so Shiki classes still apply */}
      <pre
        className={cn(
          "my-0 overflow-x-auto bg-transparent p-4 text-[13px] leading-relaxed",
          isShell && "terminal-shell",
          className,
        )}
        data-terminal={isShell ? "true" : undefined}
        {...props}
      >
        {children}
      </pre>
    </div>
  );
}
