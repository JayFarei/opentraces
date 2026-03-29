import { type HTMLAttributes, forwardRef, useState } from "react";
import { cn } from "./lib/cn";

type CodeBlockProps = HTMLAttributes<HTMLDivElement> & {
  code: string;
  copyable?: boolean;
};

const CodeBlock = forwardRef<HTMLDivElement, CodeBlockProps>(
  ({ className, code, copyable = true, children, ...props }, ref) => {
    const [copied, setCopied] = useState(false);

    const handleCopy = () => {
      navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    };

    return (
      <div ref={ref} className={cn("relative mb-5", className)} {...props}>
        <pre className="border border-border bg-bg-alt p-4 overflow-x-auto font-mono text-xs leading-relaxed text-text-muted m-0">
          <code className="bg-transparent border-none p-0 text-xs">
            {children ?? code}
          </code>
        </pre>
        {copyable && (
          <button
            type="button"
            onClick={handleCopy}
            className="absolute top-1.5 right-1.5 font-mono text-[10px] px-2 py-[3px] bg-surface border border-border text-text-dim cursor-pointer transition-all z-10 hover:text-text hover:border-border-strong active:text-accent"
          >
            {copied ? "copied" : "copy"}
          </button>
        )}
      </div>
    );
  },
);
CodeBlock.displayName = "CodeBlock";

export { CodeBlock, type CodeBlockProps };
