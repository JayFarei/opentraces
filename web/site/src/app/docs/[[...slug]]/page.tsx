import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDocContent, getDocMeta, getSkillContent, DOC_NAV } from "@/lib/docs";
import { docStructuredData } from "@/lib/structured-data";
import DocsViewer from "@/components/DocsViewer";
import JsonLd from "@/components/JsonLd";

interface Props {
  params: Promise<{ slug?: string[] }>;
}

export async function generateStaticParams() {
  return DOC_NAV.map((entry) => ({
    slug: entry.slug ? entry.slug.split("/") : [],
  }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const slugStr = slug?.join("/") || "";
  const meta = getDocMeta(slugStr);
  if (!meta) return {};
  return {
    title: meta.title,
    description: meta.description,
    alternates: { canonical: meta.canonical },
    openGraph: {
      title: meta.title,
      description: meta.description,
      url: meta.canonical,
      type: "article",
    },
  };
}

export default async function DocPage({ params }: Props) {
  const { slug } = await params;
  const slugStr = slug?.join("/") || "";
  const content = await getDocContent(slugStr);

  if (!content) notFound();

  const skillContent = getSkillContent();
  const meta = getDocMeta(slugStr);

  return (
    <>
      {meta && (
        <JsonLd
          data={docStructuredData({
            heading: meta.heading,
            description: meta.description,
            canonical: meta.canonical,
          })}
        />
      )}
      <div className="docs-breadcrumb">
        docs {slugStr && `/ ${slugStr.replace(/\//g, " / ")}`}
      </div>
      <DocsViewer content={content} skillContent={skillContent} />
    </>
  );
}
