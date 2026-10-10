import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// The default 1 s wait for findBy queries is too short on a loaded machine.
configure({ asyncUtilTimeout: 3000 });

afterEach(() => cleanup());
