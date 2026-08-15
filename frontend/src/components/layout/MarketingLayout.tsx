import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { MarketingFooter } from "./MarketingFooter";
import { Logo } from "@/components/brand/Logo";

const NAV_GROUPS = [
  { label: "Platform", links: [["Overview", "/"], ["Market Intelligence", "/agent"], ["Risk Manager", "/risk-manager"], ["Performance Lab", "/performance-lab"]] },
  { label: "Intelligence", links: [["Intelligence Engine", "/#intelligence"], ["Opportunity Analysis", "/#opportunity-preview"], ["Strategy Validation", "/performance-lab"], ["Market Correlation", "/correlation"]] },
  { label: "Resources", links: [["Security", "/security"], ["Pricing", "/pricing"], ["Contact", "/contact"]] },
  { label: "Company", links: [["About TradeCoreFX", "/about"], ["Founder", "/founder"], ["Vision and Mission", "/about#vision-mission"]] },
];

export function MarketingLayout() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-50 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
          <Link to="/" className="flex min-w-0 items-center gap-3" onClick={() => setOpen(false)}>
            <Logo className="h-10 w-10 shrink-0" />
            <span className="min-w-0"><span className="block text-base font-bold leading-tight tracking-tight">TradeCoreFX</span><span className="block truncate text-xs font-medium text-muted-foreground">Filter Better. Trade Smarter. Risk Less.</span></span>
          </Link>
          <nav className="hidden items-center gap-1 lg:flex" aria-label="Marketing navigation">
            {NAV_GROUPS.map((group) => <details key={group.label} className="group relative"><summary className="cursor-pointer list-none rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">{group.label}</summary><div className="absolute left-0 top-full mt-2 min-w-56 rounded-xl border bg-popover p-2 shadow-lg">{group.links.map(([label, to]) => <Link key={label} to={to} className="block rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground">{label}</Link>)}</div></details>)}
          </nav>
          <div className="flex items-center gap-2"><Link to="/agent" className="hidden min-h-11 items-center rounded-lg border px-4 py-2 text-sm font-semibold transition hover:bg-muted md:inline-flex">Open Platform</Link><Link to="/agent" className="hidden min-h-11 items-center rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-sm transition hover:opacity-90 sm:inline-flex">Begin Analysis</Link><button type="button" className="inline-flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground lg:hidden" aria-label={open ? "Close navigation" : "Open navigation"} aria-controls="marketing-mobile-navigation" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}</button></div>
        </div>
        <div className={cn("border-t lg:hidden", open ? "block" : "hidden")}>
          <nav id="marketing-mobile-navigation" className="mx-auto flex max-w-7xl flex-col gap-1 px-4 py-3 sm:px-6" aria-label="Mobile marketing navigation">
            {NAV_GROUPS.map((group) => <details key={group.label} className="rounded-lg border bg-card/40"><summary className="cursor-pointer px-3 py-2 text-sm font-semibold">{group.label}</summary><div className="grid gap-1 px-3 pb-2">{group.links.map(([label, to]) => <Link key={label} to={to} onClick={() => setOpen(false)} className="rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground">{label}</Link>)}</div></details>)}
            <Link to="/agent" onClick={() => setOpen(false)} className="mt-2 rounded-lg border px-4 py-2 text-center text-sm font-semibold">Open Platform</Link><Link to="/agent" onClick={() => setOpen(false)} className="rounded-lg bg-primary px-4 py-2 text-center text-sm font-semibold text-primary-foreground shadow-sm">Begin Analysis</Link>
          </nav>
        </div>
      </header>
      <main><Outlet /></main>
      <MarketingFooter />
    </div>
  );
}
