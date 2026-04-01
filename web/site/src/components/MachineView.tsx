"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useCallback } from "react";

interface FrontMatter {
  [key: string]: string;
}

function parseFrontMatter(content: string): { meta: FrontMatter; body: string } {
  const match = content.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!match) return { meta: {}, body: content };

  const meta: FrontMatter = {};
  let currentKey = "";
  let currentValue = "";
  let isMultiline = false;

  for (const line of match[1].split("\n")) {
    const keyLine = line.match(/^(\w+):\s*(>)?\s*(.*)$/);
    if (keyLine) {
      if (currentKey) meta[currentKey] = currentValue.replace(/\s+/g, " ").trim();
      currentKey = keyLine[1];
      isMultiline = !!keyLine[2];
      currentValue = keyLine[3] || "";
    } else if ((isMultiline || line.match(/^\s+/)) && currentKey) {
      currentValue += " " + line.trim();
    }
  }
  if (currentKey) meta[currentKey] = currentValue.replace(/\s+/g, " ").trim();

  return { meta, body: match[2] };
}

function headingId(text: string): string {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

export default function MachineView({ content }: { content: string }) {
  const { meta, body } = parseFrontMatter(content);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(content);
  }, [content]);

  const handleCopyLink = useCallback(() => {
    navigator.clipboard.writeText(window.location.href);
  }, []);

  return (
    <div className="machine-view">
      {/* Front matter */}
      <div className="machine-frontmatter">
        <div className="machine-fm-fence">---</div>
        {meta.name && (
          <div className="machine-fm-row">
            <span className="machine-fm-key">name</span>
            <span className="machine-fm-sep">:</span>
            <span className="machine-fm-val">{meta.name}</span>
          </div>
        )}
        <div className="machine-fm-row">
          <span className="machine-fm-key">type</span>
          <span className="machine-fm-sep">:</span>
          <span className="machine-fm-val machine-fm-type">skill</span>
        </div>
        <div className="machine-fm-row">
          <span className="machine-fm-key">category</span>
          <span className="machine-fm-sep">:</span>
          <span className="machine-fm-val">agent traces</span>
        </div>
        {meta.description && (
          <div className="machine-fm-row machine-fm-desc-row">
            <span className="machine-fm-key">description</span>
            <span className="machine-fm-sep">:</span>
            <span className="machine-fm-val machine-fm-desc">{meta.description}</span>
          </div>
        )}
        <div className="machine-fm-fence">---</div>
        <div className="machine-fm-actions">
          <button className="machine-copy-btn" onClick={handleCopy}>[copy raw]</button>
          <button className="machine-copy-btn" onClick={handleCopyLink}>[copy link]</button>
        </div>
      </div>

      {/* Skill content */}
      <div className="machine-content">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => <h1 className="machine-h1">{children}</h1>,
            h2: ({ children }) => {
              const id = headingId(String(children));
              return <h2 id={id} className="machine-h2">{children}</h2>;
            },
            h3: ({ children }) => {
              const id = headingId(String(children));
              return <h3 id={id} className="machine-h3">{children}</h3>;
            },
            a: ({ href, children }) => (
              <a href={href} className="machine-link" target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ),
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            code({ children, className, ...rest }: any) {
              const text = String(children);
              const isBlock =
                rest.node?.position?.start?.line !== rest.node?.position?.end?.line ||
                text.includes("\n");
              if (isBlock) {
                return (
                  <pre className="machine-code-block">
                    <code>{text}</code>
                  </pre>
                );
              }
              return <code className="machine-code-inline">{children}</code>;
            },
            pre({ children }) {
              return <>{children}</>;
            },
            table: ({ children }) => <table className="machine-table">{children}</table>,
            p: ({ children }) => <p className="machine-p">{children}</p>,
          }}
        >
          {body}
        </ReactMarkdown>
      </div>
    </div>
  );
}
