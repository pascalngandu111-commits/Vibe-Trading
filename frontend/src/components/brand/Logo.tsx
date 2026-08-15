import { useState } from "react";
import { BarChart3 } from "lucide-react";
import { cn } from "@/lib/utils";

const TRADECOREFX_LOGO_SRC = "/images/brand/tradecorefx-logo-mark.webp";

interface LogoProps {
  className?: string;
  alt?: string;
}

/** Shared TradeCoreFX brand asset with an accessible, non-image fallback. */
export function Logo({ className, alt = "TradeCoreFX" }: LogoProps) {
  const [available, setAvailable] = useState(true);

  if (!available) {
    return (
      <span className={cn("inline-flex items-center justify-center rounded-xl bg-primary/10 text-primary", className)} role="img" aria-label={alt}>
        <BarChart3 className="h-5 w-5" aria-hidden="true" />
      </span>
    );
  }

  return (
    <img
      src={TRADECOREFX_LOGO_SRC}
      alt={alt}
      width={512}
      height={512}
      decoding="async"
      className={cn("object-contain", className)}
      onError={() => setAvailable(false)}
    />
  );
}
