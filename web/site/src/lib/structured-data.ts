// Centralized schema.org structured data, grounded in the project's real facts
// (see root metadata, llms.txt, next.config Link headers, and the GitHub repo).
// Consumed by JsonLd <script> tags so search and AI answer engines can read a
// machine-readable statement of what opentraces is.

const SITE = "https://opentraces.ai";
const GITHUB = "https://github.com/JayFarei/opentraces";

const DESCRIPTION =
  "Open-source CLI for capturing AI coding agent session traces into a private local bucket, then authoring and publishing them as structured JSONL datasets on Hugging Face Hub.";

const organization = {
  "@type": "Organization",
  "@id": `${SITE}/#organization`,
  name: "opentraces",
  url: SITE,
  description: DESCRIPTION,
  logo: `${SITE}/og.png`,
  sameAs: [GITHUB],
};

const website = {
  "@type": "WebSite",
  "@id": `${SITE}/#website`,
  name: "open traces",
  url: SITE,
  description: DESCRIPTION,
  publisher: { "@id": `${SITE}/#organization` },
  inLanguage: "en",
};

const softwareApplication = {
  "@type": "SoftwareApplication",
  "@id": `${SITE}/#software`,
  name: "opentraces",
  url: SITE,
  applicationCategory: "DeveloperApplication",
  operatingSystem: "macOS, Linux",
  description: DESCRIPTION,
  softwareHelp: `${SITE}/docs`,
  codeRepository: GITHUB,
  programmingLanguage: "Python",
  license: "https://opensource.org/license/mit",
  isAccessibleForFree: true,
  offers: {
    "@type": "Offer",
    price: "0",
    priceCurrency: "USD",
  },
  publisher: { "@id": `${SITE}/#organization` },
  sameAs: [GITHUB],
};

// The home page graph: one document describing the org, the site, and the tool.
export const homeStructuredData = {
  "@context": "https://schema.org",
  "@graph": [organization, website, softwareApplication],
};

// The explorer: a collection page over the published trace datasets.
export const explorerStructuredData = {
  "@context": "https://schema.org",
  "@type": "CollectionPage",
  "@id": `${SITE}/explorer#page`,
  name: "Explorer — opentraces",
  description:
    "Browse live opentraces trace datasets on Hugging Face Hub: dataset stats, sample trace rows, and agent, model, and dependency breakdowns.",
  url: `${SITE}/explorer`,
  about: { "@id": `${SITE}/#software` },
  isPartOf: { "@id": `${SITE}/#website` },
  inLanguage: "en",
};

// A documentation page: a technical article plus its breadcrumb trail.
export function docStructuredData(opts: {
  heading: string;
  description: string;
  canonical: string;
}) {
  const isIndex = opts.canonical === `${SITE}/docs`;
  const crumbs = isIndex
    ? [{ name: "Documentation", item: `${SITE}/docs` }]
    : [
        { name: "Documentation", item: `${SITE}/docs` },
        { name: opts.heading, item: opts.canonical },
      ];
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "TechArticle",
        "@id": `${opts.canonical}#article`,
        headline: opts.heading,
        name: opts.heading,
        description: opts.description,
        url: opts.canonical,
        about: { "@id": `${SITE}/#software` },
        isPartOf: { "@id": `${SITE}/#website` },
        publisher: { "@id": `${SITE}/#organization` },
        inLanguage: "en",
      },
      {
        "@type": "BreadcrumbList",
        "@id": `${opts.canonical}#breadcrumb`,
        itemListElement: crumbs.map((c, i) => ({
          "@type": "ListItem",
          position: i + 1,
          name: c.name,
          item: c.item,
        })),
      },
    ],
  };
}

// Per-version schema reference page: a technical article about the trace format.
export function schemaStructuredData(opts: {
  version: string;
  date: string;
  summary: string;
  canonical: string;
}) {
  return {
    "@context": "https://schema.org",
    "@type": "TechArticle",
    "@id": `${opts.canonical}#article`,
    headline: `opentraces schema v${opts.version}`,
    name: `opentraces schema v${opts.version}`,
    description: opts.summary,
    url: opts.canonical,
    datePublished: opts.date,
    version: opts.version,
    about: { "@id": `${SITE}/#software` },
    isPartOf: { "@id": `${SITE}/#website` },
    publisher: { "@id": `${SITE}/#organization` },
    inLanguage: "en",
  };
}
