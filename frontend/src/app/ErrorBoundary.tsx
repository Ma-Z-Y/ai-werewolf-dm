import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Unexpected application error", error, info);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <main
          className="grid min-h-screen place-items-center bg-surface px-6 text-text"
          role="alert"
        >
          <p>页面出现异常，请刷新后重试。</p>
        </main>
      );
    }

    return this.props.children;
  }
}
