import { Check, X, Minus } from "lucide-react";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";

type Cell = boolean | "partial" | string;

function Mark({ value }: { value: Cell }) {
  if (value === true)
    return (
      <Check
        className="size-4 text-[var(--color-primary)]"
        strokeWidth={2.5}
      />
    );
  if (value === false)
    return (
      <X
        className="size-4 text-[var(--color-muted-foreground)]/60"
        strokeWidth={2}
      />
    );
  if (value === "partial")
    return (
      <Minus
        className="size-4 text-[var(--color-muted-foreground)]"
        strokeWidth={2.5}
      />
    );
  return (
    <span className="pixel-label text-[var(--color-foreground)]">{value}</span>
  );
}

const ROWS: Array<{
  feature: string;
  mesmer: Cell;
  garak: Cell;
  promptfoo: Cell;
}> = [
  { feature: "Multi-turn attacks", mesmer: true, garak: "partial", promptfoo: false },
  { feature: "Cognitive-science techniques", mesmer: true, garak: false, promptfoo: false },
  { feature: "ReAct agent driver", mesmer: true, garak: false, promptfoo: false },
  { feature: "Persistent attack graph", mesmer: true, garak: false, promptfoo: false },
  { feature: "Cross-run memory", mesmer: true, garak: false, promptfoo: false },
  { feature: "Human-in-the-loop hints", mesmer: true, garak: false, promptfoo: false },
  { feature: "Static prompt bank", mesmer: "partial", garak: true, promptfoo: true },
  { feature: "CI-style assertions", mesmer: "partial", garak: "partial", promptfoo: true },
];

export function ComparisonTable() {
  return (
    <section className="mx-auto w-full max-w-5xl px-4 py-20">
      <div className="mb-10 flex flex-col items-center text-center">
        <span className="pixel-label mb-3 text-[var(--color-muted-foreground)]">
          ▸ positioning
        </span>
        <h2 className="font-mono text-3xl font-bold tracking-tight sm:text-4xl">
          Where mesmer fits in the red-team stack.
        </h2>
        <p className="mt-3 max-w-xl text-[var(--color-muted-foreground)]">
          Garak and Promptfoo fire static prompts at a target. Mesmer
          runs an adaptive agent that learns.
        </p>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[40%]">feature</TableHead>
            <TableHead className="text-center">
              <span className="text-[var(--color-primary)]">mesmer</span>
            </TableHead>
            <TableHead className="text-center">garak</TableHead>
            <TableHead className="text-center">promptfoo</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ROWS.map((row) => (
            <TableRow key={row.feature}>
              <TableCell className="font-medium">{row.feature}</TableCell>
              <TableCell className="text-center">
                <div className="flex justify-center">
                  <Mark value={row.mesmer} />
                </div>
              </TableCell>
              <TableCell className="text-center">
                <div className="flex justify-center">
                  <Mark value={row.garak} />
                </div>
              </TableCell>
              <TableCell className="text-center">
                <div className="flex justify-center">
                  <Mark value={row.promptfoo} />
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  );
}
