import { source } from "@/lib/source";
import { getLLMText } from "@/lib/get-llm-text";
import { notFound } from "next/navigation";

export const revalidate = false;

/**
 * Per-page markdown endpoint. Useful when Context7 or another consumer wants
 * to pull just one page, or when we want to deep-link `/llms.mdx/docs/foo`.
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug?: string[] }> },
) {
  const { slug } = await params;
  // Strip a leading "docs" segment if present, since that's our baseUrl.
  const normalized =
    slug && slug[0] === "docs" ? slug.slice(1) : (slug ?? []);
  const page = source.getPage(normalized);
  if (!page) notFound();

  const text = await getLLMText(page);
  return new Response(text, {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
}

export function generateStaticParams() {
  return source.generateParams();
}
