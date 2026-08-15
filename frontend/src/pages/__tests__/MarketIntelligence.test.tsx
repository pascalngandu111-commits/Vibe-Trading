import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarketIntelligence } from "@/pages/MarketIntelligence";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, listSwarmRuns: vi.fn(), getSwarmRun: vi.fn() } };
});

const summary = { id:"r1", preset_name:"tradecorefx_forex_desk", status:"completed", created_at:"2026-08-14T12:00:00Z", task_count:5, completed_count:5, is_tradecorefx:true, pair:"EUR/USD", requested_horizon:"swing" };
const detail = { ...summary, completed_at:"2026-08-14T13:00:00Z", llm_provider:null, model:null, integrity_state:"VERIFIED", audit_state:"PASSED", tradecorefx_validation:{ pair:"EUR/USD", final_decision:"WAIT", evidence_label:"CAPTURED_PROVIDER", data_provider:"yfinance", capture_time:"2026-08-14T12:00:00Z", confidence:{total:50,cap:50}, binding_data_gate:{status:"PASS",reasons:[]}, binding_risk_gate:{status:"FAIL",reasons:["verified current macro support unavailable"]}, macro:{status:"UNAVAILABLE"}, audit:{passed:true}, dissent:[], recheck_condition:"Refresh failed evidence", disclosure:"Decision support only; no guarantee.", bar_evidence:Object.fromEntries(["1D","4H","1H"].map(tf=>[tf,{source:"yfinance",captured_at:"2026-08-14T12:00:00Z",last_bar_at:"2026-08-14T11:00:00Z",freshness:"FRESH",history_status:"ok",bars:220}])) } };

describe("MarketIntelligence", () => {
  beforeEach(() => { vi.mocked(api.listSwarmRuns).mockResolvedValue([summary]); vi.mocked(api.getSwarmRun).mockResolvedValue(detail); });
  it("renders binding WAIT evidence and separate provider metadata without execution claims", async () => {
    render(<MarketIntelligence/>);
    await screen.findByText("WAIT");
    expect(screen.getAllByText("yfinance").length).toBeGreaterThan(0);
    expect(screen.getByText("Unavailable / Unavailable")).toBeInTheDocument();
    expect(screen.getByText(/verified current macro support unavailable/)).toBeInTheDocument();
    for (const tf of ["1D","4H","1H"]) expect(screen.getByText(tf)).toBeInTheDocument();
    expect(screen.getByText(/not broker-executable quotes/)).toBeInTheDocument();
    expect(screen.queryByRole("button", {name:/buy|sell|execute/i})).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", {name:/refresh/i}));
    await waitFor(() => expect(api.listSwarmRuns).toHaveBeenCalledTimes(2));
  });
  it("renders empty and error states", async () => {
    vi.mocked(api.listSwarmRuns).mockResolvedValueOnce([]);
    const { unmount } = render(<MarketIntelligence/>);
    await screen.findByText("No TradeCoreFX runs yet");
    expect(screen.getByText("Decision: UNAVAILABLE")).toBeInTheDocument();
    unmount();
    vi.mocked(api.listSwarmRuns).mockRejectedValueOnce(new Error("offline"));
    render(<MarketIntelligence/>); expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  });
  it("does not render a forged directional state when the risk gate fails", async () => {
    vi.mocked(api.getSwarmRun).mockResolvedValueOnce({ ...detail, tradecorefx_validation: { ...detail.tradecorefx_validation, final_decision: "LONG_SETUP" } });
    render(<MarketIntelligence/>);
    expect(await screen.findByText("WAIT")).toBeInTheDocument();
    expect(screen.queryByText("LONG_SETUP")).not.toBeInTheDocument();
  });
  it.each([{integrity_state:"FAILED_REBUILT_SAFE"}, {audit_state:"FAILED"}] as const)("blocks directional state when verification fails: %o", async (override) => {
    vi.mocked(api.getSwarmRun).mockResolvedValueOnce({ ...detail, ...override, tradecorefx_validation: { ...detail.tradecorefx_validation, final_decision:"SHORT_SETUP", binding_risk_gate:{status:"PASS",reasons:[]}, macro:{status:"VERIFIED"} } });
    render(<MarketIntelligence/>);
    expect(await screen.findByText("WAIT")).toBeInTheDocument();
    expect(screen.queryByText("SHORT_SETUP")).not.toBeInTheDocument();
  });
  it("renders a fully consistent verified directional fixture conditionally", async () => {
    vi.mocked(api.getSwarmRun).mockResolvedValueOnce({ ...detail, tradecorefx_validation: { ...detail.tradecorefx_validation, final_decision:"LONG_SETUP", binding_risk_gate:{status:"PASS",reasons:[]}, macro:{status:"VERIFIED"} } });
    render(<MarketIntelligence/>);
    expect(await screen.findByText("LONG_SETUP")).toBeInTheDocument();
  });
  it("renders authoritative no-trade and has no execution or order controls", async () => {
    vi.mocked(api.getSwarmRun).mockResolvedValueOnce({ ...detail, tradecorefx_validation: { ...detail.tradecorefx_validation, final_decision:"NO_TRADE_DATA" } });
    render(<MarketIntelligence/>);
    expect(await screen.findByText("NO_TRADE_DATA")).toBeInTheDocument();
    expect(screen.queryByRole("button", {name:/buy|sell|execute|order/i})).not.toBeInTheDocument();
  });
  it("shows UNAVAILABLE for a degraded terminal detail and not WAIT", async () => {
    vi.mocked(api.getSwarmRun).mockResolvedValueOnce({ ...detail, tradecorefx_validation: null });
    render(<MarketIntelligence/>);
    expect(await screen.findByText("UNAVAILABLE")).toBeInTheDocument();
    expect(screen.queryByText("WAIT")).not.toBeInTheDocument();
    expect(await screen.findByText(/Degraded: structured validation is unavailable/)).toBeInTheDocument();
  });
  it("shows PENDING for pending validation and not WAIT", async () => {
    vi.mocked(api.getSwarmRun).mockResolvedValueOnce({ ...detail, status:"running", completed_at:null, integrity_state:"PENDING", audit_state:"PENDING", tradecorefx_validation:null });
    render(<MarketIntelligence/>);
    expect(await screen.findByText("PENDING")).toBeInTheDocument();
    expect(screen.queryByText("WAIT")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/Pending: validation/);
  });
  it("renders malformed timestamps as Unavailable", async () => {
    vi.mocked(api.getSwarmRun).mockResolvedValueOnce({ ...detail, completed_at:"not-a-date", tradecorefx_validation:{...detail.tradecorefx_validation, capture_time:"bad-date"} });
    render(<MarketIntelligence/>);
    await screen.findByText("WAIT");
    expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/Unavailable/).length).toBeGreaterThan(0);
  });
});
