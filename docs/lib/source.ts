import { docs } from "@/.source";
import { loader } from "fumadocs-core/source";

/**
 * fumadocs-mdx 11.10's runtime returns `files` as `() => VirtualFile[]`,
 * while fumadocs-core 15.8's `loader` destructures `source: { files }` and
 * calls `files.map(...)` — expecting an array. The *types* even disagree
 * with the *runtime* here (fumadocs-mdx type says array, chunk returns
 * function), so we cast through `unknown` and handle both shapes.
 *
 * Drop this shim when the packages converge.
 */
const mdxSource = docs.toFumadocsSource();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const filesField = (mdxSource as unknown as { files: any }).files;
const resolvedFiles: unknown[] =
  typeof filesField === "function" ? filesField() : filesField;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const patchedSource = { files: resolvedFiles } as any;

export const source = loader({
  baseUrl: "/docs",
  source: patchedSource,
});
