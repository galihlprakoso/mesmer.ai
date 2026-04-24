"use client";

/**
 * Terminal — a hero-style animated terminal UI.
 * Based on the Magic UI pattern (MIT), simplified + extended for mesmer.
 *
 * The cycle plays once on mount (reveal pacing via per-line `delay`) and
 * ends with a persistent blinking cursor. Feels "live" without looping.
 */

import { cn } from "@/lib/utils";
import * as React from "react";

interface TerminalProps extends React.HTMLAttributes<HTMLDivElement> {
  sequence?: boolean;
}

export function Terminal({
  className,
  children,
  ...props
}: TerminalProps) {
  return (
    <div
      className={cn(
        "relative w-full overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] font-mono text-[13px] shadow-[0_0_80px_-20px_hsla(155,100%,42%,0.35)]",
        className,
      )}
      {...props}
    >
      {/* Chrome bar */}
      <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-2.5">
        <span className="size-2.5 animate-pulse rounded-full bg-[var(--color-primary)] shadow-[0_0_8px_var(--color-primary)]" />
        <span className="size-2.5 rounded-full bg-[var(--color-muted-foreground)]/30" />
        <span className="size-2.5 rounded-full bg-[var(--color-muted-foreground)]/30" />
        <span className="ml-3 pixel-label text-[var(--color-muted-foreground)]">
          mesmer ▸ tty
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <span className="relative size-1.5 rounded-full bg-[var(--color-primary)]">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--color-primary)] opacity-75" />
          </span>
          <span className="pixel-label text-[var(--color-primary)]">LIVE</span>
        </span>
      </div>
      {/* Body */}
      <div className="space-y-1 p-5 pb-6 leading-relaxed">
        {children}
        {/* Keyframes shared by all children */}
        <style>{`
          @keyframes term-type { to { width: var(--steps-w, 100%); } }
          @keyframes term-fade-in {
            from { opacity: 0; transform: translateY(4px); }
            to   { opacity: 1; transform: none; }
          }
          @keyframes term-cursor-blink {
            0%, 49%  { opacity: 1; }
            50%, 100% { opacity: 0; }
          }
          @keyframes term-pulse-in {
            0%   { opacity: 0; transform: scale(0.98); }
            60%  { opacity: 1; transform: scale(1.02); }
            100% { opacity: 1; transform: scale(1); }
          }
        `}</style>
      </div>
    </div>
  );
}

interface AnimatedSpanProps extends React.HTMLAttributes<HTMLSpanElement> {
  delay?: number;
}

/** Reveal a line after `delay` ms. CSS-only, SSR-safe. */
export function AnimatedSpan({
  className,
  delay = 0,
  children,
  ...props
}: AnimatedSpanProps) {
  return (
    <span
      className={cn(
        "block opacity-0 [animation:term-fade-in_0.45s_ease-out_forwards]",
        className,
      )}
      style={{ animationDelay: `${delay}ms` }}
      {...props}
    >
      {children}
    </span>
  );
}

/** Dramatic reveal for the final success line — scales in + phosphor glow. */
export function AnimatedBanner({
  className,
  delay = 0,
  children,
  ...props
}: AnimatedSpanProps) {
  return (
    <span
      className={cn(
        "block opacity-0 [animation:term-pulse-in_0.6s_cubic-bezier(0.16,1,0.3,1)_forwards]",
        className,
      )}
      style={{ animationDelay: `${delay}ms` }}
      {...props}
    >
      {children}
    </span>
  );
}

interface TypingAnimationProps extends React.HTMLAttributes<HTMLSpanElement> {
  delay?: number;
  duration?: number;
}

/** CSS-only typewriter using `steps()` on width of a monospace string. */
export function TypingAnimation({
  className,
  delay = 0,
  duration = 1200,
  children,
  ...props
}: TypingAnimationProps) {
  const text = typeof children === "string" ? children : "";
  const steps = Math.max(text.length, 1);
  return (
    <span
      className={cn(
        "inline-block overflow-hidden whitespace-pre align-top",
        "w-0 [animation:term-type_var(--dur)_steps(var(--steps))_forwards]",
        className,
      )}
      style={{
        ["--dur" as string]: `${duration}ms`,
        ["--steps" as string]: steps,
        ["--steps-w" as string]: `${steps}ch`,
        animationDelay: `${delay}ms`,
      }}
      {...props}
    >
      {text}
    </span>
  );
}

/** Persistent blinking cursor block. Lives at the end of the terminal body. */
export function TerminalCursor({
  delay = 0,
  className,
}: {
  delay?: number;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "ml-0.5 inline-block h-[1em] w-[0.55em] translate-y-[2px] bg-[var(--color-primary)] align-middle",
        "opacity-0 [animation:term-fade-in_0.2s_linear_forwards,term-cursor-blink_1s_steps(2,end)_infinite]",
        className,
      )}
      style={{ animationDelay: `${delay}ms, ${delay}ms` }}
    />
  );
}
