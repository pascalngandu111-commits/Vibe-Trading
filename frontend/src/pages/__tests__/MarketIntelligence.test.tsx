import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarketIntelligence } from "@/pages/MarketIntelligence";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, listSwarmRuns: vi.fn(), getSwarmRun: vi.fn(), createTradeCoreFXRun: vi.fn() } };
});

const summary = { id:"r1", preset_name:"tradecorefx_forex_desk", status:"completed", created_at:"2026-08-14T12:00:00Z", task_count:5, completed_count:5, is_tradecorefx:true, pair:"EUR/USD", requested_horizon:"swing" };
const detail = { ...summary, completed_at:"2026-08-14T13:00:00Z", llm_provider:null, model:null, integrity_state:"VERIFIED", audit_state:"PASSED", tradecorefx_validation:{ pair:"EUR/USD", final_decision:"WAIT", evidence_label:"CAPTURED_PROVIDER", data_provider:"yfinance", capture_time:"2026-08-14T12:00:00Z", confidence:{total:50,cap:50}, binding_data_gate:{status:"PASS",reasons:[]}, binding_risk_gate:{status:"FAIL",reasons:["verified current macro support unavailable"]}, macro:{status:"UNAVAILABLE"}, audit:{passed:true}, dissent:[], recheck_condition:"Refresh failed evidence", disclosure:"Decision support only; no guarantee.", bar_evidence:Object.fromEntries(["1D","4H","1H"].map(tf=>[tf,{source:"yfinance",captured_at:"2026-08-14T12:00:00Z",last_bar_at:"2026-08-14T11:00:00Z",freshness:"FRESH",history_status:"ok",bars:220}])) } };
const created = {id:"r-new", pair:"EUR/USD", requested_horizon:"multi-timeframe 1D/4H/1H", status:"pending", navigation:{detail_path:"/market-intelligence?run=r-new"}};
const pending = {...detail, id:"r-new", status:"pending", integrity_state:"PENDING", audit_state:"PENDING", tradecorefx_validation:null};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("MarketIntelligence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listSwarmRuns).mockResolvedValue([summary]);
    vi.mocked(api.getSwarmRun).mockResolvedValue(detail);
    vi.mocked(api.createTradeCoreFXRun).mockResolvedValue(created);
  });
  it("requests the TradeCoreFX preset from the server", async () => {
    render(<MarketIntelligence/>);
    await screen.findByText("WAIT");
    expect(api.listSwarmRuns).toHaveBeenCalledWith("tradecorefx_forex_desk");
  });
  it("submits a pair once, refreshes the filtered list, and selects the pending run", async () => {
    let resolveCreate!: (value: Awaited<ReturnType<typeof api.createTradeCoreFXRun>>) => void;
    const create = new Promise<Awaited<ReturnType<typeof api.createTradeCoreFXRun>>>(resolve => { resolveCreate = resolve; });
    vi.mocked(api.createTradeCoreFXRun).mockReturnValueOnce(create);
    vi.mocked(api.listSwarmRuns).mockResolvedValueOnce([summary]).mockResolvedValueOnce([{...summary,id:"r-new",status:"pending"}]);
    vi.mocked(api.getSwarmRun).mockResolvedValueOnce(detail).mockResolvedValueOnce(pending);
    render(<MarketIntelligence/>); await screen.findByText("WAIT");
    const button = screen.getByRole("button", {name:"Start analysis"});
    await userEvent.click(button); await userEvent.click(button);
    expect(api.createTradeCoreFXRun).toHaveBeenCalledTimes(1);
    resolveCreate(created);
    expect(await screen.findByText("EUR/USD analysis pending.")).toBeInTheDocument();
    expect(api.listSwarmRuns).toHaveBeenLastCalledWith("tradecorefx_forex_desk");
    expect(await screen.findByText("PENDING")).toBeInTheDocument();
  });
  it("clears initial loading for a failed submission and ignores the late initial response", async () => {
    const initialList = deferred<typeof summary[]>();
    const createRequest = deferred<Awaited<ReturnType<typeof api.createTradeCoreFXRun>>>();
    vi.mocked(api.listSwarmRuns).mockReturnValueOnce(initialList.promise);
    vi.mocked(api.createTradeCoreFXRun).mockReturnValueOnce(createRequest.promise);
    render(<MarketIntelligence/>);

    expect(screen.getByText("Loading market intelligence…")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", {name:"Start analysis"}));
    expect(screen.queryByText("Loading market intelligence…")).not.toBeInTheDocument();
    createRequest.reject(new Error("create failed"));
    expect(await screen.findByRole("alert")).toHaveTextContent("create failed");

    initialList.resolve([summary]);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("create failed"));
    expect(api.getSwarmRun).not.toHaveBeenCalled();
  });
  it("selects a created run and ignores a late initial response", async () => {
    const initialList = deferred<typeof summary[]>();
    const createRequest = deferred<Awaited<ReturnType<typeof api.createTradeCoreFXRun>>>();
    vi.mocked(api.listSwarmRuns).mockReturnValueOnce(initialList.promise).mockResolvedValueOnce([{...summary, id:"r-new", status:"pending"}]);
    vi.mocked(api.createTradeCoreFXRun).mockReturnValueOnce(createRequest.promise);
    vi.mocked(api.getSwarmRun).mockResolvedValueOnce(pending);
    render(<MarketIntelligence/>);

    await userEvent.click(screen.getByRole("button", {name:"Start analysis"}));
    createRequest.resolve(created);
    expect(await screen.findByText("PENDING")).toBeInTheDocument();
    expect(screen.queryByText("Loading market intelligence…")).not.toBeInTheDocument();

    initialList.resolve([summary]);
    await waitFor(() => expect(screen.getByText("PENDING")).toBeInTheDocument());
    expect(screen.queryByText("WAIT")).not.toBeInTheDocument();
  });
  it.each(["success", "error"] as const)("lets submission supersede a stale refresh %s", async (outcome) => {
    const refresh = deferred<typeof summary[]>();
    vi.mocked(api.listSwarmRuns)
      .mockResolvedValueOnce([summary])
      .mockReturnValueOnce(refresh.promise)
      .mockResolvedValueOnce([{...summary, id:"r-new", status:"pending"}]);
    vi.mocked(api.getSwarmRun).mockResolvedValueOnce(detail).mockResolvedValueOnce(pending);
    render(<MarketIntelligence/>);
    await screen.findByText("WAIT");

    await userEvent.click(screen.getByRole("button", {name:/refresh/i}));
    expect(screen.getByText("Loading market intelligence…")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", {name:"Start analysis"}));
    expect(await screen.findByText("PENDING")).toBeInTheDocument();

    if (outcome === "success") refresh.resolve([summary]);
    else refresh.reject(new Error("stale refresh failed"));
    await waitFor(() => expect(screen.getByText("PENDING")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Loading market intelligence…")).not.toBeInTheDocument();
  });
  it("shows custom pair state and submits the visible custom input", async () => {
    render(<MarketIntelligence/>); await screen.findByText("WAIT");
    const input = screen.getByLabelText("FX pair");
    await userEvent.clear(input); await userEvent.type(input, "EUR/GBP");
    expect(screen.getByLabelText("Common beta pairs")).toHaveValue("");
    expect(screen.getByRole("option", {name:"Custom pair"})).toHaveProperty("selected", true);
    await userEvent.click(screen.getByRole("button", {name:"Start analysis"}));
    expect(api.createTradeCoreFXRun).toHaveBeenCalledWith("EUR/GBP");
  });
  it("updates the visible input and submission when a common pair is selected", async () => {
    render(<MarketIntelligence/>); await screen.findByText("WAIT");
    const input = screen.getByLabelText("FX pair");
    await userEvent.clear(input); await userEvent.type(input, "EUR/GBP");
    await userEvent.selectOptions(screen.getByLabelText("Common beta pairs"), "EUR/USD");
    expect(input).toHaveValue("EUR/USD");
    await userEvent.click(screen.getByRole("button", {name:"Start analysis"}));
    expect(api.createTradeCoreFXRun).toHaveBeenCalledWith("EUR/USD");
  });
  it("keeps lowercase compact input visibly custom until server normalization", async () => {
    vi.mocked(api.createTradeCoreFXRun).mockResolvedValueOnce({...created, pair:"EUR/GBP"});
    render(<MarketIntelligence/>); await screen.findByText("WAIT");
    const input = screen.getByLabelText("FX pair");
    await userEvent.clear(input); await userEvent.type(input, "eurgbp");
    expect(screen.getByLabelText("Common beta pairs")).toHaveValue("");
    expect(input).toHaveValue("eurgbp");
    await userEvent.click(screen.getByRole("button", {name:"Start analysis"}));
    expect(api.createTradeCoreFXRun).toHaveBeenCalledWith("eurgbp");
    expect(await screen.findByText("EUR/GBP analysis pending.")).toBeInTheDocument();
  });
  it("announces validation and server errors accessibly", async () => {
    vi.mocked(api.createTradeCoreFXRun).mockRejectedValueOnce(new Error("Enter a supported beta FX pair"));
    render(<MarketIntelligence/>); await screen.findByText("WAIT");
    const input = screen.getByLabelText("FX pair"); await userEvent.clear(input); await userEvent.type(input, "BTC/USD");
    await userEvent.click(screen.getByRole("button", {name:"Start analysis"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a supported beta FX pair");
  });
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
  it("keeps the latest manual selection when detail promises settle out of order", async () => {
    const secondSummary = { ...summary, id: "r2", pair: "GBP/USD" };
    const deferred: Record<string, { promise: Promise<typeof detail>; resolve: (value: typeof detail) => void; reject: (reason?: unknown) => void }> = {};
    const makeDeferred = (id: string) => {
      let resolve!: (value: typeof detail) => void;
      let reject!: (reason?: unknown) => void;
      const promise = new Promise<typeof detail>((res, rej) => { resolve = res; reject = rej; });
      deferred[id] = { promise, resolve, reject };
      return promise;
    };
    let initialLoad = true;
    vi.mocked(api.listSwarmRuns).mockResolvedValue([summary, secondSummary]);
    vi.mocked(api.getSwarmRun).mockImplementation((id) => {
      if (initialLoad) { initialLoad = false; return Promise.resolve(detail); }
      return deferred[id]?.promise ?? makeDeferred(id);
    });
    render(<MarketIntelligence/>);
    await screen.findByText("WAIT");

    const buttons = screen.getAllByRole("button", { name: /EUR\/USD|GBP\/USD/ });
    await userEvent.click(buttons[0]);
    await userEvent.click(buttons[1]);
    deferred.r2.resolve({ ...detail, id: "r2", requested_horizon: "latest-selection" });
    await screen.findByText(/latest-selection/);
    deferred.r1.reject(new Error("stale failure"));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByText(/latest-selection/)).toBeInTheDocument();
  });
  it("ignores a stale successful detail response after the latest selection", async () => {
    const secondSummary = { ...summary, id: "r2", pair: "GBP/USD" };
    let initialLoad = true;
    let resolveFirst!: (value: typeof detail) => void;
    let resolveSecond!: (value: typeof detail) => void;
    const first = new Promise<typeof detail>((resolve) => { resolveFirst = resolve; });
    const second = new Promise<typeof detail>((resolve) => { resolveSecond = resolve; });
    vi.mocked(api.listSwarmRuns).mockResolvedValue([summary, secondSummary]);
    vi.mocked(api.getSwarmRun).mockImplementation((id) => {
      if (initialLoad) { initialLoad = false; return Promise.resolve(detail); }
      return id === "r1" ? first : second;
    });
    render(<MarketIntelligence/>);
    await screen.findByText("WAIT");

    const buttons = screen.getAllByRole("button", { name: /EUR\/USD|GBP\/USD/ });
    await userEvent.click(buttons[0]);
    await userEvent.click(buttons[1]);
    resolveSecond({ ...detail, id: "r2", requested_horizon: "latest-success" });
    await screen.findByText(/latest-success/);
    resolveFirst({ ...detail, id: "r1", requested_horizon: "stale-success" });
    await waitFor(() => expect(screen.queryByText(/stale-success/)).not.toBeInTheDocument());
    expect(screen.getByText(/latest-success/)).toBeInTheDocument();
  });
  it("does not let a stale refresh detail replace a newer manual selection", async () => {
    const secondSummary = { ...summary, id: "r2", pair: "GBP/USD" };
    let initialLoad = true;
    let resolveRefresh!: (value: typeof detail) => void;
    let resolveManual!: (value: typeof detail) => void;
    const refreshDetail = new Promise<typeof detail>((resolve) => { resolveRefresh = resolve; });
    const manualDetail = new Promise<typeof detail>((resolve) => { resolveManual = resolve; });
    vi.mocked(api.listSwarmRuns).mockResolvedValue([summary, secondSummary]);
    vi.mocked(api.getSwarmRun).mockImplementation((id) => {
      if (initialLoad) { initialLoad = false; return Promise.resolve(detail); }
      return id === "r1" ? refreshDetail : manualDetail;
    });
    render(<MarketIntelligence/>);
    await screen.findByText("WAIT");

    await userEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(api.getSwarmRun).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole("button", { name: /GBP\/USD/ }));
    resolveManual({ ...detail, id: "r2", requested_horizon: "manual-latest" });
    await screen.findByText(/manual-latest/);
    resolveRefresh({ ...detail, id: "r1", requested_horizon: "refresh-stale" });
    await waitFor(() => expect(screen.queryByText(/refresh-stale/)).not.toBeInTheDocument());
    expect(screen.getByText(/manual-latest/)).toBeInTheDocument();
  });
  it("re-enables submission when a manual selection supersedes an in-flight create", async () => {
    const secondSummary = { ...summary, id: "r2", pair: "GBP/USD" };
    let resolveCreate!: (value: Awaited<ReturnType<typeof api.createTradeCoreFXRun>>) => void;
    const create = new Promise<Awaited<ReturnType<typeof api.createTradeCoreFXRun>>>((resolve) => { resolveCreate = resolve; });
    vi.mocked(api.createTradeCoreFXRun).mockReturnValueOnce(create);
    vi.mocked(api.listSwarmRuns).mockResolvedValue([summary, secondSummary]);
    vi.mocked(api.getSwarmRun).mockImplementation((id) => Promise.resolve(
      id === "r2" ? { ...detail, id: "r2", requested_horizon: "manual-newest" } : detail,
    ));
    render(<MarketIntelligence/>);
    await screen.findByText("WAIT");

    await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
    expect(screen.getByRole("button", { name: "Starting…" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /GBP\/USD/ }));
    await screen.findByText(/manual-newest/);
    resolveCreate({ id:"r-new", pair:"EUR/USD", requested_horizon:"multi-timeframe 1D/4H/1H", status:"pending", navigation:{detail_path:"/market-intelligence?run=r-new"} });

    await waitFor(() => expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled());
    expect(screen.getByText(/manual-newest/)).toBeInTheDocument();
    expect(api.listSwarmRuns).toHaveBeenCalledTimes(1);
  });
});
