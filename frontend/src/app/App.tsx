import { ErrorBoundary } from "./ErrorBoundary";
import { AppRoutes } from "./routes";

export function App() {
  return (
    <ErrorBoundary>
      <AppRoutes />
    </ErrorBoundary>
  );
}
