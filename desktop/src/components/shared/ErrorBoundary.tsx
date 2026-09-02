import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  /** Changes (e.g. the route path) clear a caught error and re-arm the one-shot auto-retry. */
  resetKey?: unknown;
}

interface State {
  hasError: boolean;
  error: Error | null;
  retrying: boolean;
}

const AUTO_RETRY_MS = 1000;

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, retrying: false };
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private autoRetried = false;

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[ErrorBoundary] ${error.message}`, error, info.componentStack);
    // A transient render-loop during the initial WS burst used to leave "Page Error" on screen
    // on every route until the user clicked Retry. Retry once by ourselves; if it throws
    // again we stay in the fallback with the manual button.
    if (!this.autoRetried) {
      this.autoRetried = true;
      this.setState({ retrying: true });
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null;
        this.setState({ hasError: false, error: null, retrying: false });
      }, AUTO_RETRY_MS);
    }
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey) {
      this.autoRetried = false;
      if (this.state.hasError) this.reset();
    }
  }

  componentWillUnmount() {
    this.clearTimer();
  }

  private clearTimer() {
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }

  reset = () => {
    this.clearTimer();
    this.setState({ hasError: false, error: null, retrying: false });
  };

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback || (
          <div style={{
            padding: 32,
            color: "#FF4757",
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 13,
            background: "#0B1120",
            borderRadius: 12,
            margin: 16,
          }}>
            <h3 style={{ color: "#F0F4FA", marginBottom: 8 }}>Page Error</h3>
            <pre style={{ whiteSpace: "pre-wrap", opacity: 0.8 }}>
              {this.state.error?.message}
            </pre>
            {this.state.retrying && (
              <p style={{ color: "#8898AA", marginTop: 8 }}>Retrying automatically…</p>
            )}
            <button
              onClick={this.reset}
              style={{
                marginTop: 12,
                padding: "6px 16px",
                background: "#00D4AA",
                color: "#050810",
                border: "none",
                borderRadius: 8,
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              Retry
            </button>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
