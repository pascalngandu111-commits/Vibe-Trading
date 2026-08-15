import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MarketingLayout } from "../MarketingLayout";

describe("MarketingLayout", () => {
  it("renders real marketing navigation links", () => {
    render(<MemoryRouter><Routes><Route element={<MarketingLayout />}><Route path="/" element={<div>Home</div>} /></Route></Routes></MemoryRouter>);
    expect(screen.getAllByRole("link", { name: "Begin Analysis" })[0]).toHaveAttribute("href", "/agent");
    expect(screen.getAllByRole("link", { name: "Open Platform" })[0]).toHaveAttribute("href", "/agent");
    expect(screen.getAllByRole("link", { name: "Pricing" })[0]).toHaveAttribute("href", "/pricing");
    expect(screen.getByText("Privacy Policy")).toHaveTextContent("Coming Soon");
  });
});
