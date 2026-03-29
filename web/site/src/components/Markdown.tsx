"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useCallback } from "react";

function CodeBlock({ children, className }: { children: string; className?: string }) {
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(children.replace(/\n$/, ""));
  }, [children]);

  return (
    <div className="code-block-wrap">
      <button className="code-copy-btn" onClick={handleCopy} title="Copy">
        [cp]
      </button>
      <pre>
        <code className={className}>{children}</code>
      </pre>
    </div>
  );
}

export default function Markdown({ content }: { content: string }) {
  return (
    <div className="docs-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          code({ children, className, ...rest }: any) {
            const text = String(children);

            const isBlock = rest.node?.position?.start?.line !== rest.node?.position?.end?.line
              || text.includes("\n");
            if (isBlock) {
              return <CodeBlock className={className}>{text}</CodeBlock>;
            }
            return <code className={className}>{children}</code>;
          },
          // Unwrap the extra <pre> that react-markdown adds around code blocks
          pre({ children }) {
            return <>{children}</>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
