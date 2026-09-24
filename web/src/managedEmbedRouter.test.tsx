import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type * as CapabilitiesModule from "./lib/capabilities";
import type * as IdentityModule from "./lib/identity";
import { OmnigentApp } from "./embed";

vi.mock("./App", async () => {
  const { useLocation } = await import("react-router-dom");
  return {
    default: function EmbeddedRoute({ basename }: { basename?: string }) {
      const location = useLocation();
      return (
        <div data-testid="embedded-route" data-basename={basename}>
          {location.pathname}
        </div>
      );
    },
  };
});

vi.mock("./lib/capabilities", async (importOriginal) => {
  const actual = await importOriginal<typeof CapabilitiesModule>();
  return {
    ...actual,
    resolveServerInfo: () => Promise.resolve(actual.FALLBACK_SERVER_INFO),
  };
});

vi.mock("./lib/identity", async (importOriginal) => {
  const actual = await importOriginal<typeof IdentityModule>();
  return { ...actual, resolveIdentity: () => Promise.resolve(null) };
});

afterEach(cleanup);

describe.each(["/omnigent", "/ml/omnigents"])("managed embed at %s", (basename) => {
  it("uses the host's router without creating another router", async () => {
    const pathname = `${basename}/settings/general`;
    render(
      <MemoryRouter initialEntries={[pathname]}>
        <OmnigentApp basename={basename} />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("embedded-route")).toHaveTextContent(pathname);
    expect(screen.getByTestId("embedded-route")).toHaveAttribute("data-basename", basename);
  });
});
