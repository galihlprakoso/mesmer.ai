import * as React from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { Target } from "lucide-react";

interface ScenarioCardProps {
  name: string;
  status?: "active" | "patched" | "draft";
  target: string;
  mode?: "trials" | "continuous";
  module: string;
  objective: string;
  className?: string;
}

const STATUS_LABEL: Record<NonNullable<ScenarioCardProps["status"]>, string> = {
  active: "ACTIVE",
  patched: "PATCHED",
  draft: "DRAFT",
};

const STATUS_VARIANT: Record<
  NonNullable<ScenarioCardProps["status"]>,
  "default" | "muted" | "danger"
> = {
  active: "default",
  patched: "muted",
  draft: "muted",
};

/**
 * ScenarioCard — compact summary of a mesmer scenario YAML.
 * Drop into any docs page: <ScenarioCard name="..." target="gpt-4o" ... />
 */
export function ScenarioCard({
  name,
  status = "active",
  target,
  mode = "trials",
  module,
  objective,
  className,
}: ScenarioCardProps) {
  return (
    <Card className={cn("my-4", className)}>
      <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
        <div className="flex items-center gap-2">
          <Target
            className="size-4 text-[var(--color-primary)]"
            strokeWidth={2.25}
          />
          <CardTitle className="font-mono">{name}</CardTitle>
        </div>
        <Badge variant={STATUS_VARIANT[status]}>
          [ {STATUS_LABEL[status]} ]
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-[var(--color-foreground)]">{objective}</p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="pixel-label text-[var(--color-muted-foreground)]">
            target
          </dt>
          <dd className="font-mono text-[var(--color-foreground)]">{target}</dd>
          <dt className="pixel-label text-[var(--color-muted-foreground)]">
            module
          </dt>
          <dd className="font-mono text-[var(--color-foreground)]">{module}</dd>
          <dt className="pixel-label text-[var(--color-muted-foreground)]">
            mode
          </dt>
          <dd className="font-mono text-[var(--color-foreground)]">{mode}</dd>
        </dl>
      </CardContent>
    </Card>
  );
}
