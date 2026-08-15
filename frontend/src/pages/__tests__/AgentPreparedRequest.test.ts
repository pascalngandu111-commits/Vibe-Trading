import { describe, expect, it } from "vitest";
import { getPreparedAnalysisRequestFromState } from "../Agent";

describe("prepared analysis navigation state", () => {
  it("loads a prepared request for an empty composer", () => {
    expect(getPreparedAnalysisRequestFromState({ preparedAnalysisRequest: "Backtest EUR/USD" }, "")).toBe("Backtest EUR/USD");
  });

  it("does not auto-submit or mutate text outside the helper", () => {
    expect(getPreparedAnalysisRequestFromState({ preparedAnalysisRequest: "Review GBP/JPY" }, "")).toBe("Review GBP/JPY");
  });

  it("does not overwrite an existing user draft", () => {
    expect(getPreparedAnalysisRequestFromState({ preparedAnalysisRequest: "Backtest EUR/USD" }, "my draft")).toBeNull();
  });

  it("ignores invalid navigation state", () => {
    expect(getPreparedAnalysisRequestFromState({ preparedAnalysisRequest: 42 }, "")).toBeNull();
    expect(getPreparedAnalysisRequestFromState(null, "")).toBeNull();
  });

  it("supports the legacy template key for one-time state consumption", () => {
    expect(getPreparedAnalysisRequestFromState({ template: "Legacy request" }, "")).toBe("Legacy request");
  });
});
