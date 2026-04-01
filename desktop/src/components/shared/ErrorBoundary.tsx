import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

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
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
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
