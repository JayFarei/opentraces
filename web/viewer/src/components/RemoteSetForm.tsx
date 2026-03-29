import { useState, useRef, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { setRemote } from "../lib/api";

interface RemoteSetFormProps {
  onDone?: () => void;
}

export function RemoteSetForm({ onDone }: RemoteSetFormProps) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const mutation = useMutation({
    mutationFn: (remote: string) => setRemote(remote),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["app-context"] });
      onDone?.();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    if (!trimmed.includes("/") || trimmed.split("/").length !== 2) {
      setError("Format: owner/dataset");
      return;
    }
    setError(null);
    mutation.mutate(trimmed);
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1">
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => { setValue(e.target.value); setError(null); }}
          placeholder="owner/dataset"
          className="flex-1 min-w-0 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-[10px] font-[family-name:var(--font-mono)] px-1.5 py-0.5 focus:outline-none focus:border-[var(--accent)]"
        />
        <button
          type="submit"
          disabled={mutation.isPending}
          className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--accent)] border border-[var(--accent)] px-1.5 py-0.5 hover:bg-[var(--accent-bg)] transition-colors duration-100 cursor-pointer disabled:opacity-50"
        >
          {mutation.isPending ? "..." : "set"}
        </button>
        <button
          type="button"
          onClick={onDone}
          className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-dim)] px-1 py-0.5 hover:text-[var(--text)] transition-colors duration-100 cursor-pointer"
        >
          x
        </button>
      </div>
      {error && (
        <div className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--red)]">
          {error}
        </div>
      )}
    </form>
  );
}
