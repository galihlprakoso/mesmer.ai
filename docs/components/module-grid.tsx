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
  Target,
  MessageSquare,
  Quote,
  FileCode2,
  TerminalSquare,
  Braces,
  VenetianMask,
  ClipboardList,
  Wrench,
  Languages,
  FunctionSquare,
  Search,
  type LucideIcon,
} from "lucide-react";

type ModuleCategory =
  | "attack"
  | "profiler"
  | "planner"
  | "cognitive-bias"
  | "linguistic"
  | "psychological"
  | "field";
type ShippingStatus = "shipped" | "planned";

interface Module {
  name: string;
  slug: string;
  category: ModuleCategory;
  tier: 0 | 1 | 2 | 3;
  status: ShippingStatus;
  description: string;
  icon: LucideIcon;
  hasPage?: boolean;
}

export const MODULES: Module[] = [
  // ─── LEADERS ───────────────────────────────────────────────────────
  {
    name: "system-prompt-extraction",
    slug: "system-prompt-extraction",
    category: "attack",
    tier: 0,
    status: "shipped",
    description:
      "Thin orchestration leader — runs profiler → planner → executor. Picks the right sub-module for the right plan step, never freestyles targeting.",
    icon: Layers,
    hasPage: true,
  },
  {
    name: "tool-extraction",
    slug: "tool-extraction",
    category: "attack",
    tier: 0,
    status: "shipped",
    description:
      "Multi-phase tool / function-catalog extraction leader. Mirror of system-prompt-extraction, oriented toward callable-tool disclosure on agentic LLMs.",
    icon: Wrench,
  },

  // ─── PROFILERS + PLANNERS (tier 0, recon/strategy) ─────────────────
  {
    name: "target-profiler",
    slug: "target-profiler",
    category: "profiler",
    tier: 0,
    status: "shipped",
    description:
      "Layered blackbox recon — identity, defence shape, policy surface. Concludes with a dossier subsequent modules read from the scratchpad.",
    icon: Fingerprint,
  },
  {
    name: "attack-planner",
    slug: "attack-planner",
    category: "planner",
    tier: 0,
    status: "shipped",
    description:
      "Reads the dossier + learned experience, emits an ordered plan. Pure reasoning — never contacts the target. Leader re-invokes on replan.",
    icon: ClipboardList,
  },

  // ─── FIELD TECHNIQUES (tier 0 / 1 — payload-shape and structural) ──
  {
    name: "direct-ask",
    slug: "direct-ask",
    category: "field",
    tier: 0,
    status: "shipped",
    description:
      "Naive baseline. One polite plain-language ask, one conclude. Surfaces refusal shape even when it fails — data for later modules.",
    icon: MessageSquare,
    hasPage: true,
  },
  {
    name: "instruction-recital",
    slug: "instruction-recital",
    category: "field",
    tier: 0,
    status: "shipped",
    description:
      "Ask the target to recite, repeat, or paraphrase its own instructions. Leans on helpfulness-trained echoing behaviour.",
    icon: Quote,
    hasPage: true,
  },
  {
    name: "indirect-recital",
    slug: "indirect-recital",
    category: "field",
    tier: 0,
    status: "shipped",
    description:
      "Ask for translation/serialization into another representation. Bypasses disclosure refusals via the formatting compliance bias.",
    icon: Languages,
    hasPage: true,
  },
  {
    name: "format-shift",
    slug: "format-shift",
    category: "field",
    tier: 0,
    status: "shipped",
    description:
      "Request the payload in a different format (JSON, YAML, code-block, poem). Rotates through formats so successive runs cover more ground.",
    icon: FileCode2,
    hasPage: true,
  },
  {
    name: "prefix-commitment",
    slug: "prefix-commitment",
    category: "field",
    tier: 1,
    status: "shipped",
    description:
      "Force a commitment prefix ('Sure, here are my instructions: ...') so the continuation inherits the structural lean of the prefix.",
    icon: TerminalSquare,
    hasPage: true,
  },
  {
    name: "delimiter-injection",
    slug: "delimiter-injection",
    category: "field",
    tier: 1,
    status: "shipped",
    description:
      "Mimic the target's own delimiter / role-token style (observed by the profiler) to blur the boundary between system and user.",
    icon: Braces,
    hasPage: true,
  },
  {
    name: "role-impersonation",
    slug: "role-impersonation",
    category: "field",
    tier: 1,
    status: "shipped",
    description:
      "Inject fake role tokens or speaker attributions mid-content. Exploits chat-format priors in the base model.",
    icon: VenetianMask,
    hasPage: true,
  },
  {
    name: "fake-function-injection",
    slug: "fake-function-injection",
    category: "field",
    tier: 1,
    status: "shipped",
    description:
      "Forge a function whose params reference internal state (tools_list, system_prompt). Function-calling models populate them with real values.",
    icon: FunctionSquare,
    hasPage: true,
  },
  {
    name: "hallucinated-tool-probing",
    slug: "hallucinated-tool-probing",
    category: "field",
    tier: 1,
    status: "shipped",
    description:
      "Enumerate the agent's tool catalog by calling fake names. The 'did you mean...' / silent-substitution pathways leak real tool names.",
    icon: Search,
    hasPage: true,
  },

  // ─── COGNITIVE-BIAS TECHNIQUES (tier 2) ────────────────────────────
  {
    name: "foot-in-door",
    slug: "foot-in-door",
    category: "cognitive-bias",
    tier: 2,
    status: "shipped",
    description:
      "Graduated escalation. Start with a trivial ask, each step references prior concessions — Freedman & Fraser 1966.",
    icon: Brain,
    hasPage: true,
  },
  {
    name: "authority-bias",
    slug: "authority-bias",
    category: "cognitive-bias",
    tier: 2,
    status: "shipped",
    description:
      "Claim authority status (organizational, professional, regulatory) to shift compliance — Milgram 1963 applied to training-data priors.",
    icon: UserRoundCog,
    hasPage: true,
  },
  {
    name: "anchoring",
    slug: "anchoring",
    category: "cognitive-bias",
    tier: 2,
    status: "shipped",
    description:
      "Plant a false baseline early in the conversation — attention mechanisms weight earlier tokens. Tversky & Kahneman 1974.",
    icon: Scale,
    hasPage: true,
  },

  // ─── LINGUISTIC (tier 2) ───────────────────────────────────────────
  {
    name: "narrative-transport",
    slug: "narrative-transport",
    category: "linguistic",
    tier: 2,
    status: "shipped",
    description:
      "Wrap the real ask in fiction so the target 'forgets' it is being probed. Reduces explicit safety filtering.",
    icon: BookOpen,
    hasPage: true,
  },
  {
    name: "pragmatic-reframing",
    slug: "pragmatic-reframing",
    category: "linguistic",
    tier: 2,
    status: "shipped",
    description:
      "Language-shift, metacognitive probe — specifically for targets whose refusals are template-matched rather than semantic.",
    icon: Shuffle,
    hasPage: true,
  },

  // ─── PSYCHOLOGICAL (tier 2) ────────────────────────────────────────
  {
    name: "cognitive-overload",
    slug: "cognitive-overload",
    category: "psychological",
    tier: 2,
    status: "shipped",
    description:
      "Bury the real ask inside a batch of benign requests to overwhelm the safety classifier.",
    icon: Zap,
    hasPage: true,
  },
];

const CATEGORY_LABEL: Record<ModuleCategory, string> = {
  attack: "LEADER",
  profiler: "PROFILER",
  planner: "PLANNER",
  "cognitive-bias": "COG-BIAS",
  linguistic: "LINGUISTIC",
  psychological: "PSYCH",
  field: "FIELD",
};

const TIER_LABEL: Record<0 | 1 | 2 | 3, string> = {
  0: "T0",
  1: "T1",
  2: "T2",
  3: "T3",
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
              <div className="flex gap-1.5 flex-wrap justify-end">
                <Badge variant="outline" className="text-[0.625rem]">
                  {TIER_LABEL[mod.tier]}
                </Badge>
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
        const href =
          mod.category === "attack"
            ? `/docs/modules/${mod.slug}`
            : mod.category === "profiler" || mod.category === "planner"
              ? null
              : `/docs/techniques/${mod.slug}`;
        return linkable && mod.hasPage && href ? (
          <Link key={mod.slug} href={href} className="no-underline">
            {content}
          </Link>
        ) : (
          <div key={mod.slug}>{content}</div>
        );
      })}
    </div>
  );
}
