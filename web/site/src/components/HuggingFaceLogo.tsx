// Official Hugging Face logo mark (full color). Brand guidelines:
// https://huggingface.co/brand — the asset lives at public/hf-logo.svg.

type Props = { className?: string; size?: number };

export default function HuggingFaceLogo({ className, size = 16 }: Props) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src="/hf-logo.svg"
      alt="Hugging Face"
      width={size}
      height={size}
      className={className}
      draggable={false}
    />
  );
}
