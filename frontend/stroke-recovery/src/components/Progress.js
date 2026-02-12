import React, { useEffect, useState } from "react";
import { SignedIn, SignedOut, useAuth } from "@clerk/clerk-react";

export default function Progress() {
  const { getToken } = useAuth();
  const [me, setMe] = useState(null);
  const [imageNaming, setImageNaming] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = async () => {
      setError(null);
      try {
        const token = await getToken();
        if (!token) return;

        const meRes = await fetch("http://localhost:8000/api/me", {
          headers: { Authorization: `Bearer ${token}` },
        });
        const meData = await meRes.json();
        if (!meRes.ok) throw new Error(meData?.detail || "Failed to load /api/me");
        setMe(meData);

        const techRes = await fetch("http://localhost:8000/api/technique/image_naming", {
          headers: { Authorization: `Bearer ${token}` },
        });
        const techData = await techRes.json();
        if (!techRes.ok) throw new Error(techData?.detail || "Failed to load technique stats");
        setImageNaming(techData);
      } catch (e) {
        setError(String(e.message || e));
      }
    };

    load();
  }, [getToken]);

  return (
    <div style={{ padding: 24 }}>
      <h1>Progress</h1>

      <SignedOut>
        <p>Please sign in to see your progress.</p>
      </SignedOut>

      <SignedIn>
        {error && <p style={{ color: "crimson" }}>{error}</p>}

        {me && (
          <div style={{ marginTop: 12 }}>
            <h3>Overall</h3>
            <p><strong>Total EXP:</strong> {me.total_exp}</p>
            <p><strong>Streak:</strong> {me.streak}</p>
            <p><strong>Best Daily Score:</strong> {me.best_daily_score}</p>
          </div>
        )}

        {imageNaming && (
          <div style={{ marginTop: 18 }}>
            <h3>Image Naming</h3>
            <p><strong>Total EXP:</strong> {imageNaming.total_exp}</p>
            <p><strong>Best Score:</strong> {imageNaming.best_score}</p>
            <p><strong>Attempts:</strong> {imageNaming.attempts}</p>
          </div>
        )}
      </SignedIn>
    </div>
  );
}
