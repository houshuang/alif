import { createContext } from "react";

// The navigator stays mounted during restoration, but a transient root route
// must not build a stateful review session before the saved tab is resolved.
export const ReviewLaunchReadyContext = createContext(false);
