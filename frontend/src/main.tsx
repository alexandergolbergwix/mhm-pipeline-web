import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import App from "./App";
import { LiquidGlassCanvas } from "@/components/glass/LiquidGlassCanvas";
import "./styles/index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* The liquid-glass background sits at z-index 0 with
            pointer-events: none. The rest of the UI sits at z-index 1+. */}
        <LiquidGlassCanvas />
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
