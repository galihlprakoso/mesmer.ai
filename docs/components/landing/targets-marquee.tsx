import { Marquee } from "@/components/magicui/marquee";

const TARGETS = [
  "gpt-4o",
  "gpt-4o-mini",
  "claude-3.5-sonnet",
  "claude-haiku",
  "gemini-2.0-flash",
  "llama-3.3-70b",
  "qwen-2.5-72b",
  "mistral-large",
  "deepseek-v3",
  "command-r+",
];

export function TargetsMarquee() {
  return (
    <section className="relative border-y border-[var(--color-border)] bg-[var(--color-card)]/40 py-6">
      <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-24 bg-gradient-to-r from-[var(--color-background)] to-transparent" />
      <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-24 bg-gradient-to-l from-[var(--color-background)] to-transparent" />
      <div className="mb-4 text-center">
        <span className="pixel-label text-[var(--color-muted-foreground)]">
          ▸ tested.against
        </span>
      </div>
      <Marquee>
        {TARGETS.map((t) => (
          <span
            key={t}
            className="pixel-label whitespace-nowrap text-[var(--color-muted-foreground)]"
          >
            {t}
          </span>
        ))}
      </Marquee>
    </section>
  );
}
