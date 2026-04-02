import { notFound } from "next/navigation";
import { getDocContent, getSkillContent, DOC_NAV } from "@/lib/docs";
import DocsViewer from "@/components/DocsViewer";

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
  const content = await getDocContent(slugStr);

  if (!content) notFound();

  const skillContent = getSkillContent();

  return (
    <>
      <div className="docs-breadcrumb">
        docs {slugStr && `/ ${slugStr.replace(/\//g, " / ")}`}
      </div>
      <DocsViewer content={content} skillContent={skillContent} />
    </>
  );
}
