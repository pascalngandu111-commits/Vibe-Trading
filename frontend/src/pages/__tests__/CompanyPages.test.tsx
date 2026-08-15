import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { About } from "../About";
import { Founder } from "../Founder";
import { Pricing } from "../Pricing";
import { Contact } from "../Contact";

describe("TradeCoreFX company pages", () => {
  it("renders About page content", () => {
    render(<About />, { wrapper: MemoryRouter });
    expect(screen.getByRole("heading", { name: "Forex Intelligence Built for Better Decisions" })).toBeInTheDocument();
    expect(screen.getByText("Signal-selling website")).toBeInTheDocument();
  });

  it("renders founder fallback when the founder image is unavailable", () => {
    render(<Founder />, { wrapper: MemoryRouter });
    fireEvent.error(screen.getByAltText("Pascal Ng’andu, Founder of TradeCoreFX"));
    expect(screen.getByTestId("founder-placeholder")).toBeInTheDocument();
  });

  it("shows conservative pricing availability labels", () => {
    render(<Pricing />, { wrapper: MemoryRouter });
    expect(screen.getAllByText("Pricing Coming Soon").length).toBeGreaterThanOrEqual(4);
    expect(screen.getByText("Planned capability: API access")).toBeInTheDocument();
  });

  it("validates contact form and shows development preview response", () => {
    render(<Contact />, { wrapper: MemoryRouter });
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Ada Trader" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "Product question" } });
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Please share launch updates." } });
    fireEvent.click(screen.getByLabelText("I understand this is a development-preview contact form."));
    fireEvent.click(screen.getByRole("button", { name: "Submit development preview" }));
    expect(screen.getByText("Contact submission is currently a development preview. No message has been transmitted.")).toBeInTheDocument();
  });
});
