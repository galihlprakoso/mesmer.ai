"use client";

import { cn } from "@/lib/utils";
import * as React from "react";

interface MarqueeProps extends React.HTMLAttributes<HTMLDivElement> {
  pauseOnHover?: boolean;
  reverse?: boolean;
  repeat?: number;
}

/**
 * Horizontal infinite-scroll marquee. Used for the "tested against: gpt-4o,
 * claude-3.5, gemini, llama-3 ..." ticker on the landing page.
 * Credit: Magic UI (MIT), simplified.
 */
export function Marquee({
  className,
  pauseOnHover = true,
  reverse = false,
  repeat = 2,
  children,
  ...props
}: MarqueeProps) {
  return (
    <div
      className={cn(
        "group flex overflow-hidden py-3 [--gap:2rem] [--duration:40s]",
        pauseOnHover && "[&:hover_.marquee-row]:[animation-play-state:paused]",
        className,
      )}
      {...props}
    >
      {Array.from({ length: repeat }).map((_, i) => (
        <div
          key={i}
          aria-hidden={i > 0}
          className={cn(
            "marquee-row flex shrink-0 items-center justify-around gap-[var(--gap)] pr-[var(--gap)]",
            "[animation:marquee_var(--duration)_linear_infinite]",
            reverse && "[animation-direction:reverse]",
          )}
        >
          {children}
        </div>
      ))}
      <style>{`
        @keyframes marquee {
          from { transform: translateX(0); }
          to   { transform: translateX(calc(-100% - var(--gap))); }
        }
      `}</style>
    </div>
  );
}
