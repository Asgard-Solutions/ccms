/**
 * Google sign-in callback handler.
 *
 * This page is loaded after Emergent auth redirects back to
 * `${origin}/auth/google/callback#session_id=…`. We:
 *   1) Read `session_id` synchronously during render (in the parent
 *      router) — by the time this component mounts, the fragment is
 *      still on the URL.
 *   2) POST it to `/api/auth/google/exchange` to mint our JWT cookies.
 *   3) Refresh the AuthContext + navigate to /.
 *
 * REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS,
 * THIS BREAKS THE AUTH.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, ShieldX } from "lucide-react";
import { Button } from "../components/ui/button";
import { useAuth } from "../contexts/AuthContext";
import { googleExchange } from "../api/integrations";

export default function GoogleAuthCallback() {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const handled = useRef(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;

    const hash = window.location.hash || "";
    const match = hash.match(/session_id=([^&]+)/);
    const sessionId = match ? decodeURIComponent(match[1]) : null;

    if (!sessionId) {
      setError("No Google session found. Please try signing in again.");
      return;
    }

    (async () => {
      try {
        await googleExchange(sessionId);
        // Strip fragment so a refresh doesn't re-trigger this page.
        window.history.replaceState({}, "", "/auth/google/callback");
        await refresh();
        navigate("/", { replace: true });
      } catch (err) {
        const msg = err?.response?.data?.detail
          || "We couldn't complete Google sign-in.";
        setError(msg);
      }
    })();
  }, [navigate, refresh]);

  return (
    <div
      data-testid="google-callback-page"
      className="min-h-screen flex items-center justify-center bg-gradient-to-b from-background to-muted px-6"
    >
      <div className="w-full max-w-sm rounded-md border border-border bg-card shadow-sm p-8 text-center">
        {error ? (
          <>
            <ShieldX className="mx-auto h-10 w-10 text-destructive" />
            <h1 className="mt-4 text-xl font-display tracking-tight">
              Sign-in failed
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">{error}</p>
            <Button
              className="mt-6 w-full"
              onClick={() => navigate("/login", { replace: true })}
              data-testid="google-callback-back-btn"
            >
              Back to sign in
            </Button>
          </>
        ) : (
          <>
            <Loader2 className="mx-auto h-8 w-8 animate-spin text-primary" />
            <p className="mt-4 text-sm text-muted-foreground">
              Finishing Google sign-in…
            </p>
          </>
        )}
      </div>
    </div>
  );
}
