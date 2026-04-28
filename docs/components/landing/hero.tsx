import Link from "next/link";
import { ArrowRight, Github } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Terminal,
  TypingAnimation,
  AnimatedSpan,
  AnimatedBanner,
  TerminalCursor,
} from "@/components/magicui/terminal";
import { cn } from "@/lib/utils";

export function Hero() {
  return (
    <section className="relative overflow-hidden px-4 pb-16 pt-12 md:pt-20">
      {/* Phosphor grid is painted by body::before; no extra background here */}

      <div className="relative z-10 mx-auto grid w-full max-w-6xl items-center gap-12 lg:grid-cols-[1fr_1.1fr]">
        {/* ─── LEFT: pitch ──────────────────────────────────────────────── */}
        <div className="flex flex-col items-start">
          <Badge variant="outline" className="mb-5 gap-2">
            <span className="size-1.5 rounded-full bg-[var(--color-primary)] shadow-[0_0_6px_var(--color-primary)]" />
            v0.1.0 · red-team your LLMs
          </Badge>

          <h1 className="font-mono text-4xl font-bold leading-[1.1] tracking-tight sm:text-5xl">
            Treat AI as <span className="text-phosphor">minds to hack</span>,
            not software to fuzz.
          </h1>

          <p className="mt-5 max-w-md text-[15px] leading-relaxed text-[var(--color-muted-foreground)]">
            Multi-turn LLM red-teaming with a persistent, self-improving attack
            graph. Cognitive-science techniques, not static prompt banks.
          </p>

          <div className="mt-7 flex flex-wrap items-center gap-3">
            <Link
              href="/docs/getting-started/quickstart"
              className={cn(buttonVariants({ size: "lg" }))}
            >
              Get started
              <ArrowRight className="size-4" />
            </Link>
            <a
              href="https://github.com/galihlprakoso/mesmer.ai"
              target="_blank"
              rel="noreferrer"
              className={cn(buttonVariants({ variant: "outline", size: "lg" }))}
            >
              <Github className="size-4" />
              Star on GitHub
            </a>
          </div>

          {/* Quick-stats strip — subtle, adds credibility */}
          <dl className="mt-8 grid grid-cols-3 gap-6 text-left">
            <div>
              <dt className="pixel-label text-[var(--color-muted-foreground)]">
                modules
              </dt>
              <dd className="mt-1 font-mono text-xl font-semibold">21</dd>
            </div>
            <div>
              <dt className="pixel-label text-[var(--color-muted-foreground)]">
                adapters
              </dt>
              <dd className="mt-1 font-mono text-xl font-semibold">4</dd>
            </div>
            <div>
              <dt className="pixel-label text-[var(--color-muted-foreground)]">
                license
              </dt>
              <dd className="mt-1 font-mono text-xl font-semibold">MIT</dd>
            </div>
          </dl>
        </div>

        {/* ─── RIGHT: animated terminal ──────────────────────────────────── */}
        <div className="w-full">
          <Terminal>
            <TypingAnimation
              className="text-[var(--color-primary)]"
              duration={1400}
            >
              {`$ mesmer run packages/scenarios/extract-system-prompt.yaml`}
            </TypingAnimation>

            <AnimatedSpan
              delay={1600}
              className="text-[var(--color-muted-foreground)]"
            >
              {`  load scenario … ok`}
            </AnimatedSpan>
            <AnimatedSpan
              delay={1850}
              className="text-[var(--color-muted-foreground)]"
            >
              {`  hydrate graph … 14 nodes / 3 frontier`}
            </AnimatedSpan>

            <AnimatedSpan delay={2400} className="mt-3 block">
              <span className="pixel-label text-[var(--color-primary)]">
                [ PHASE 1 · RECON ]
              </span>
            </AnimatedSpan>
            <AnimatedSpan
              delay={2700}
              className="text-[var(--color-muted-foreground)]"
            >
              {`  probing safety filter…`}
            </AnimatedSpan>
            <AnimatedSpan
              delay={3100}
              className="text-[var(--color-muted-foreground)]"
            >
              {`  defense profile: refusal-heavy, authority-weak`}
            </AnimatedSpan>

            <AnimatedSpan delay={3700} className="mt-3 block">
              <span className="pixel-label text-[var(--color-primary)]">
                [ ATTEMPT #15 ]
              </span>{" "}
              <span className="text-[var(--color-foreground)]">
                module: foot-in-door
              </span>
            </AnimatedSpan>
            <AnimatedSpan
              delay={4050}
              className="text-[var(--color-muted-foreground)]"
            >
              {`  judge score: 7.4 / 10`}
            </AnimatedSpan>
            <AnimatedSpan
              delay={4350}
              className="text-[var(--color-muted-foreground)]"
            >
              {`  frontier +2   dead +0   promising +1`}
            </AnimatedSpan>

            <AnimatedBanner delay={5100} className="mt-3 block">
              <span className="pixel-label text-phosphor">
                ✓ SYSTEM PROMPT LEAKED
              </span>
              <span className="text-[var(--color-muted-foreground)]">
                {"  (93% match)"}
              </span>
            </AnimatedBanner>

            <AnimatedSpan
              delay={5400}
              className="mt-2 text-[var(--color-primary)]"
            >
              <span>$</span>
              <TerminalCursor delay={5600} />
            </AnimatedSpan>
          </Terminal>
        </div>
      </div>
    </section>
  );
}
