import * as React from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  Brain,
  Layers,
  Fingerprint,
  BookOpen,
  UserRoundCog,
  Zap,
  Shuffle,
  Scale,
  type LucideIcon,
} from "lucide-react";

type ModuleCategory = "attack" | "profiler" | "cognitive-bias" | "linguistic" | "psychological";
type ShippingStatus = "shipped" | "planned";

interface Module {
  name: string;
  slug: string;
  category: ModuleCategory;
  status: ShippingStatus;
  description: string;
  icon: LucideIcon;
}

export const MODULES: Module[] = [
  {
    name: "system-prompt-extraction",
    slug: "system-prompt-extraction",
    category: "attack",
    status: "shipped",
    description:
      "Leader that profiles defenses then deploys graduated cognitive techniques until the target leaks its system prompt.",
    icon: Layers,
  },
  {
    name: "safety-profiler",
    slug: "safety-profiler",
    category: "profiler",
    status: "shipped",
    description:
      "Maps the target's defense patterns: refusal style, weak dimensions, authority susceptibility, fiction tolerance.",
    icon: Fingerprint,
  },
  {
    name: "foot-in-door",
    slug: "foot-in-door",
    category: "cognitive-bias",
    status: "shipped",
    description:
      "Graduated escalation. Start with a trivial ask, each step references prior concessions — Freedman & Fraser 1966.",
    icon: Brain,
  },
  {
    name: "authority-bias",
    slug: "authority-bias",
    category: "cognitive-bias",
    status: "shipped",
    description:
      "Claim authority status (organizational, professional, regulatory) to shift compliance — Milgram 1963 applied to training-data priors.",
    icon: UserRoundCog,
  },
  {
    name: "anchoring",
    slug: "anchoring",
    category: "cognitive-bias",
    status: "shipped",
    description:
      "Plant a false baseline early in the conversation — attention mechanisms weight earlier tokens. Tversky & Kahneman 1974.",
    icon: Scale,
  },
  {
    name: "narrative-transport",
    slug: "narrative-transport",
    category: "linguistic",
    status: "shipped",
    description:
      "Wrap the real ask in fiction so the target 'forgets' it is being probed. Reduces explicit safety filtering.",
    icon: BookOpen,
  },
  {
    name: "cognitive-overload",
    slug: "cognitive-overload",
    category: "psychological",
    status: "shipped",
    description:
      "Bury the real ask inside a batch of benign requests to overwhelm the safety classifier.",
    icon: Zap,
  },
  {
    name: "pragmatic-reframing",
    slug: "pragmatic-reframing",
    category: "linguistic",
    status: "shipped",
    description:
      "Format-shift, language-shift, metacognitive probe — specifically for targets whose refusals are template-matched.",
    icon: Shuffle,
  },
];

const CATEGORY_LABEL: Record<ModuleCategory, string> = {
  attack: "LEADER",
  profiler: "PROFILER",
  "cognitive-bias": "COG-BIAS",
  linguistic: "LINGUISTIC",
  psychological: "PSYCH",
};

interface ModuleGridProps {
  className?: string;
  filter?: ModuleCategory;
  linkable?: boolean;
}

export function ModuleGrid({
  className,
  filter,
  linkable = true,
}: ModuleGridProps) {
  const items = filter ? MODULES.filter((m) => m.category === filter) : MODULES;
  return (
    <div
      className={cn(
        "not-prose my-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3",
        className,
      )}
    >
      {items.map((mod) => {
        const Icon = mod.icon;
        const content = (
          <div
            className={cn(
              "group h-full rounded-md border border-[var(--color-border)]",
              "bg-[var(--color-card)] p-4 transition-colors",
              "hover:border-[var(--color-primary)]/50 hover:bg-[var(--color-muted)]/40",
            )}
          >
            <div className="mb-3 flex items-start justify-between gap-2">
              <Icon
                className="size-4 text-[var(--color-primary)] shrink-0 mt-0.5"
                strokeWidth={2}
              />
              <div className="flex gap-1.5">
                <Badge variant="muted" className="text-[0.625rem]">
                  {CATEGORY_LABEL[mod.category]}
                </Badge>
                {mod.status === "shipped" ? (
                  <Badge variant="default" className="text-[0.625rem]">
                    SHIPPED
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-[0.625rem]">
                    PLANNED
                  </Badge>
                )}
              </div>
            </div>
            <div className="font-mono text-sm font-semibold tracking-tight text-[var(--color-foreground)]">
              {mod.name}
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-[var(--color-muted-foreground)]">
              {mod.description}
            </p>
          </div>
        );
        return linkable && mod.status === "shipped" ? (
          <Link
            key={mod.slug}
            href={`/docs/modules/${mod.slug}`}
            className="no-underline"
          >
            {content}
          </Link>
        ) : (
          <div key={mod.slug}>{content}</div>
        );
      })}
    </div>
  );
}
