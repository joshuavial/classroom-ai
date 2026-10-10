import { render, screen } from "@testing-library/react";
import Home from "@/app/page";

test("home page renders the placeholder heading", () => {
  render(<Home />);
  expect(screen.getByRole("heading", { name: "classroom-ai" })).toBeInTheDocument();
});
