import { source } from "@/lib/source";

export const revalidate = false;

/**
 * llms.txt — index of all documentation pages in llms.txt standard format.
 * See https://llmstxt.org/. Consumed by Context7 + other LLM ingestion tools.
 */
export async function GET() {
  const pages = source.getPages();
  const lines: string[] = [
    "# mesmer",
    "",
    "> Cognitive hacking toolkit for LLMs — multi-turn red-teaming with a persistent, self-improving attack graph.",
    "",
    "## Docs",
    "",
  ];

  for (const page of pages) {
    const data = page.data as unknown as {
      title: string;
      description?: string;
    };
    const desc = data.description ? `: ${data.description}` : "";
    lines.push(
      `- [${data.title}](https://mesmer.vercel.app${page.url}.mdx)${desc}`,
    );
  }

  return new Response(lines.join("\n"), {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}
