import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-sm px-2 py-0.5 pixel-label font-medium transition-colors",
  {
    variants: {
      variant: {
        default:
          "bg-[var(--color-primary)]/10 text-[var(--color-primary)] border border-[var(--color-primary)]/40",
        outline:
          "border border-[var(--color-border)] text-[var(--color-muted-foreground)]",
        muted:
          "bg-[var(--color-muted)] text-[var(--color-muted-foreground)] border border-[var(--color-border)]",
        danger:
          "bg-[var(--color-destructive)]/10 text-[var(--color-destructive)] border border-[var(--color-destructive)]/40",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { badgeVariants };
