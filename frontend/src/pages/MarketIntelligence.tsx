import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Database, RefreshCw } from "lucide-react";
import { api, type SwarmRunDetail, type SwarmRunSummary, type TradeCoreFXDecision } from "@/lib/api";

type TradeCoreFXDisplayState = TradeCoreFXDecision | "PENDING" | "UNAVAILABLE";

const unavailable = (value?: string | null) => value || "Unavailable";
const stamp = (value?: string | null) => {
  if (!value) return "Unavailable";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Unavailable" : parsed.toLocaleString();
};

const safeDecision = (selected: SwarmRunDetail | null): TradeCoreFXDisplayState => {
  if (!selected) return "UNAVAILABLE";
  const report = selected?.tradecorefx_validation;
  if (!report) return selected.integrity_state === "PENDING" ? "PENDING" : "UNAVAILABLE";
  const reportedDecision = report?.final_decision;
  if (reportedDecision === "NO_TRADE_DATA") return "NO_TRADE_DATA";
  if ((reportedDecision === "LONG_SETUP" || reportedDecision === "SHORT_SETUP")
    && selected?.integrity_state === "VERIFIED"
    && selected?.audit_state === "PASSED"
    && report?.audit?.passed === true
    && report?.binding_data_gate?.status === "PASS"
    && report?.binding_risk_gate?.status === "PASS"
    && report?.macro?.status === "VERIFIED") {
    return reportedDecision;
  }
  return "WAIT";
};

export function MarketIntelligence() {
  const [runs, setRuns] = useState<SwarmRunSummary[]>([]);
  const [selected, setSelected] = useState<SwarmRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const list = (await api.listSwarmRuns()).filter((run) => run.is_tradecorefx || run.preset_name === "tradecorefx_forex_desk");
      setRuns(list);
      setSelected(list[0] ? await api.getSwarmRun(list[0].id) : null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Market intelligence is unavailable"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const choose = async (id: string) => { try { setError(null); setSelected(await api.getSwarmRun(id)); } catch { setError("Run detail is unavailable"); } };
  const report = selected?.tradecorefx_validation;
  const decision = safeDecision(selected);
  return <main className="min-h-screen p-4 md:p-8"><div className="mx-auto max-w-7xl space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4 border-b pb-5"><div><p className="text-xs font-semibold uppercase tracking-[.2em] text-primary">TradeCoreFX</p><h1 className="text-3xl font-bold">Market Intelligence</h1><p className="mt-2 max-w-3xl text-sm text-muted-foreground">Authenticated, deterministic decision support. Conditional setup states are not orders, predictions, or guarantees.</p></div><button aria-label="Refresh TradeCoreFX runs" onClick={() => void load()} className="inline-flex items-center gap-2 rounded-md border px-4 py-2"><RefreshCw className="h-4 w-4"/>Refresh</button></header>
    {loading && <div role="status" className="rounded-md border p-8">Loading market intelligence…</div>}
    {!loading && error && <div role="alert" className="rounded-md border border-amber-500/40 p-5"><AlertTriangle className="inline h-5 w-5"/> {error}</div>}
    {!loading && !error && runs.length === 0 && <div className="rounded-md border border-dashed p-10 text-center"><Database className="mx-auto h-8 w-8"/><h2 className="mt-3 font-semibold">No TradeCoreFX runs yet</h2><p className="mt-2 font-semibold">Decision: {decision}</p><p className="text-sm text-muted-foreground">Run the TradeCoreFX Forex Desk to capture evidence.</p></div>}
    {runs.length > 0 && <div className="grid gap-6 lg:grid-cols-[20rem_1fr]"><nav aria-label="TradeCoreFX runs" className="space-y-2">{runs.map(run => <button key={run.id} onClick={() => void choose(run.id)} className="w-full rounded-md border p-4 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"><span className="font-semibold">{unavailable(run.pair)}</span><span className="float-right text-xs">{run.status}</span><span className="mt-2 block text-xs text-muted-foreground">{stamp(run.created_at)} · {unavailable(run.requested_horizon)}</span></button>)}</nav>
    <section className="space-y-5" aria-live="polite">{selected && <><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Tile label="Decision" value={decision}/><Tile label="Confidence" value={report?.confidence?.total != null ? `${report.confidence.total} / cap ${report.confidence.cap}` : "Unavailable"}/><Tile label="Pair / horizon" value={`${unavailable(report?.pair)} · ${unavailable(selected.requested_horizon)}`}/><Tile label="Run" value={`${selected.status} · ${stamp(selected.completed_at)}`}/></div>
    {!report && <div role="status" className="rounded-md border border-amber-500/40 p-4">{selected.integrity_state === "PENDING" ? "Pending: validation has not been persisted yet. Directional display is blocked." : "Degraded: structured validation is unavailable. No directional state is shown."}</div>}
    {report && <><div className="grid gap-4 md:grid-cols-2"><Gate title="Binding data gate" gate={report.binding_data_gate}/><Gate title="Binding risk gate" gate={report.binding_risk_gate}/></div><div className="rounded-md border p-5"><h2 className="font-semibold">Evidence and provenance</h2><dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2"><Meta label="Market-data provider" value={unavailable(report.data_provider)}/><Meta label="LLM provider / model" value={`${unavailable(selected.llm_provider)} / ${unavailable(selected.model)}`}/><Meta label="Evidence label" value={unavailable(report.evidence_label)}/><Meta label="Captured at" value={stamp(report.capture_time)}/><Meta label="Macro" value={unavailable(report.macro?.status)}/><Meta label="Audit / integrity" value={`${report.audit?.passed ? "PASSED" : "FAILED"} / ${unavailable(selected.integrity_state)}`}/></dl></div>
    <div className="overflow-x-auto rounded-md border"><table className="w-full min-w-[620px] text-left text-sm"><caption className="p-4 text-left font-semibold">Timeframe evidence</caption><thead className="bg-muted"><tr>{["Timeframe","Provider","Captured","Last bar","Freshness","History","Bars"].map(x=><th key={x} className="p-3">{x}</th>)}</tr></thead><tbody>{["1D","4H","1H"].map(tf=>{const e=report.bar_evidence?.[tf]; return <tr key={tf} className="border-t"><td className="p-3 font-medium">{tf}</td><td className="p-3">{unavailable(e?.source)}</td><td className="p-3">{stamp(e?.captured_at)}</td><td className="p-3">{stamp(e?.last_bar_at)}</td><td className="p-3">{unavailable(e?.freshness)}</td><td className="p-3">{unavailable(e?.history_status)}</td><td className="p-3">{e?.bars ?? "Unavailable"}</td></tr>})}</tbody></table></div>
    <div className="rounded-md border bg-muted/30 p-5 text-sm"><p><strong>Dissent:</strong> {report.dissent?.length ? report.dissent.join("; ") : "None recorded"}</p><p className="mt-2"><strong>Recheck:</strong> {unavailable(report.recheck_condition)}</p><p className="mt-3 text-muted-foreground">{report.disclosure} Public Yahoo Finance bars are captured public-provider research evidence, not broker-executable quotes. The LLM narrative is unaudited and is not used as binding evidence.</p></div></>}</>}</section></div>}
  </div></main>;
}
function Tile({label,value}:{label:string,value:string}) { return <div className="rounded-md border p-4"><p className="text-xs uppercase text-muted-foreground">{label}</p><p className="mt-2 font-semibold">{value}</p></div> }
function Meta({label,value}:{label:string,value:string}) { return <div><dt className="text-muted-foreground">{label}</dt><dd className="font-medium">{value}</dd></div> }
function Gate({title,gate}:{title:string,gate?:{status?:string;reasons?:string[]}}) { return <div className="rounded-md border p-5"><h2 className="font-semibold">{title}: {unavailable(gate?.status)}</h2>{gate?.reasons?.length ? <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">{gate.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul> : <p className="mt-2 text-sm text-muted-foreground">No binding failures.</p>}</div> }
