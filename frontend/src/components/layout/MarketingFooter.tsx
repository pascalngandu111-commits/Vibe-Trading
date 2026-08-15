import { Link } from "react-router-dom";

const disclosure = "TradeCoreFX provides market-analysis, research, risk-management and educational tools. Information displayed by the platform is not financial advice and does not guarantee future performance.";

export function MarketingFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="border-t bg-card/40 px-4 py-10 sm:px-6 lg:px-8">
      <div className="mx-auto grid max-w-7xl gap-8 lg:grid-cols-[1.2fr_2fr]">
        <div>
          <div className="max-w-[300px]">
            <img
              src="/images/brand/tradecorefx-logo-light.webp"
              alt="TradeCoreFX — Filter Better. Trade Smarter. Risk Less."
              width={900}
              height={300}
              loading="lazy"
              decoding="async"
              className="h-auto w-full object-contain object-left dark:hidden"
            />
            <img
              src="/images/brand/tradecorefx-logo-dark.webp"
              alt=""
              aria-hidden="true"
              width={900}
              height={300}
              loading="lazy"
              decoding="async"
              className="hidden h-auto w-full object-contain object-left dark:block"
            />
          </div>
          <p className="mt-4 max-w-md text-sm leading-6 text-muted-foreground">{disclosure}</p>
          <p className="mt-4 text-xs text-muted-foreground">© {year} TradeCoreFX. All rights reserved.</p>
        </div>
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          <FooterColumn title="Platform" links={[["Market Intelligence", "/agent"], ["Risk Manager", "/risk-manager"], ["Performance Lab", "/performance-lab"], ["Opportunity Reports", "/reports"], ["Pricing", "/pricing"]]} />
          <FooterColumn title="Resources" links={[["Security", "/security"], ["Contact", "/contact"], ["Intelligence Engine", "/#intelligence"], ["Risk Disclosure", "/security#known-limitations"]]} />
          <FooterColumn title="Company" links={[["About", "/about"], ["Founder", "/founder"], ["Vision and Mission", "/about#vision-mission"]]} />
          <div>
            <h2 className="text-sm font-semibold">Legal</h2>
            <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
              {['Privacy Policy', 'Terms of Service', 'Cookie Policy'].map((item) => <li key={item}>{item} <span className="text-xs">Coming Soon</span></li>)}
              <li><Link to="/security#known-limitations" className="hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">Risk Disclosure</Link></li>
            </ul>
          </div>
        </div>
      </div>
    </footer>
  );
}

function FooterColumn({ title, links }: { title: string; links: Array<[string, string]> }) {
  return <div><h2 className="text-sm font-semibold">{title}</h2><ul className="mt-3 space-y-2 text-sm text-muted-foreground">{links.map(([label, to]) => <li key={label}><Link to={to} className="hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">{label}</Link></li>)}</ul></div>;
}
