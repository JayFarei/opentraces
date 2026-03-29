import { notFound } from "next/navigation";
import { getDocContent, DOC_NAV } from "@/lib/docs";
import Markdown from "@/components/Markdown";

interface Props {
  params: Promise<{ slug?: string[] }>;
}

export async function generateStaticParams() {
  return DOC_NAV.map((entry) => ({
    slug: entry.slug ? entry.slug.split("/") : [],
  }));
}

export default async function DocPage({ params }: Props) {
  const { slug } = await params;
  const slugStr = slug?.join("/") || "";
  const content = getDocContent(slugStr);

  if (!content) notFound();

  return (
    <>
      <div className="docs-breadcrumb">
        docs {slugStr && `/ ${slugStr.replace(/\//g, " / ")}`}
      </div>
      <Markdown content={content} />
    </>
  );
}
