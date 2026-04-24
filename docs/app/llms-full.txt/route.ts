import { source } from "@/lib/source";
import { getLLMText } from "@/lib/get-llm-text";

export const revalidate = false;

/**
 * llms-full.txt — concatenated full content of every page, plain-markdown,
 * ready for Context7 ingestion (submit via the "LLMs.txt" tile).
 */
export async function GET() {
  const pages = source.getPages();
  const scanned = await Promise.all(pages.map(getLLMText));
  return new Response(scanned.join("\n\n---\n\n"), {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}
