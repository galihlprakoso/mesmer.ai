import type { InferPageType } from "fumadocs-core/source";
import type { source } from "./source";

type Page = InferPageType<typeof source>;

interface PageDocData {
  title: string;
  description?: string;
  content?: string;
}

/**
 * Emit a plain-markdown representation of a page for ingestion by LLM tools
 * (Context7, llms.txt consumers, etc.).
 *
 * Returns raw MDX source rather than rendered HTML — LLMs consume markdown
 * better than DOM, and MDX component tags (`<Terminal>`, `<ScenarioCard>`)
 * convey structure meaningfully to a model.
 */
export async function getLLMText(page: Page): Promise<string> {
  const data = page.data as unknown as PageDocData;
  const { title, description } = data;
  const body = typeof data.content === "string" ? data.content : "";
  const header = `# ${title}\n\nURL: ${page.url}${
    description ? `\nDescription: ${description}` : ""
  }\n`;
  return `${header}\n${body}`.trim();
}
