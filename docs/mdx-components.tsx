import defaultMdxComponents from "fumadocs-ui/mdx";
import type { MDXComponents } from "mdx/types";
import { TerminalPre } from "@/components/terminal-pre";
import { ScenarioCard } from "@/components/scenario-card";
import { ModuleGrid } from "@/components/module-grid";
import { AsciiDiagram } from "@/components/ascii-diagram";
import { Kbd } from "@/components/kbd";
import { Terminal } from "@/components/magicui/terminal";

/**
 * Merge order matters: spread Fumadocs defaults FIRST, then override.
 * Components listed below become usable unqualified inside any .mdx file.
 */
export function getMDXComponents(components?: MDXComponents): MDXComponents {
  return {
    ...defaultMdxComponents,
    // Override <pre> with our terminal-themed variant
    pre: (props) => <TerminalPre {...props} />,
    // Mesmer-specific MDX primitives
    ScenarioCard,
    ModuleGrid,
    AsciiDiagram,
    Kbd,
    Terminal,
    ...components,
  };
}
