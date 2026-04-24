import * as React from "react";
import { source } from "@/lib/source";
import { getMDXComponents } from "@/mdx-components";
import {
  DocsPage,
  DocsBody,
  DocsDescription,
  DocsTitle,
} from "fumadocs-ui/page";
import { notFound } from "next/navigation";

// The fumadocs-core/mdx version-shim in @/lib/source loses the rich DocOut
// typing. We assert back to the runtime shape we know is there.
interface DocData {
  title: string;
  description?: string;
  body: React.ComponentType<{ components?: ReturnType<typeof getMDXComponents> }>;
  toc?: Parameters<typeof DocsPage>[0]["toc"];
  full?: boolean;
}

export default async function Page(props: {
  params: Promise<{ slug?: string[] }>;
}) {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  const data = page.data as unknown as DocData;
  const MDX = data.body;

  return (
    <DocsPage toc={data.toc} full={data.full}>
      <DocsTitle>{data.title}</DocsTitle>
      {data.description && <DocsDescription>{data.description}</DocsDescription>}
      <DocsBody>
        <MDX components={getMDXComponents()} />
      </DocsBody>
    </DocsPage>
  );
}

export function generateStaticParams() {
  return source.generateParams();
}

export async function generateMetadata(props: {
  params: Promise<{ slug?: string[] }>;
}) {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();
  const data = page.data as unknown as DocData;
  return {
    title: data.title,
    description: data.description,
  };
}
