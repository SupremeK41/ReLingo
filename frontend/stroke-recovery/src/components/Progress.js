// frontend/src/components/Progress.js
import React, { useEffect, useMemo, useState } from "react";
import { SignedIn, SignedOut, useAuth } from "@clerk/clerk-react";
import "./Progress.css";

const TECHNIQUES = [
  {
    id: "image_naming",
    label: "Image Naming",
    tip: "Tip: If a word is hard, pause, breathe, then try again slowly — accuracy beats speed.",
  },
  {
    id: "sentence_repetition",
    label: "Sentence Repetition",
    tip: "Tip: Focus on the key content words first, then add the small connecting words.",
  },
];

// Simple leveling curve (tweak later)
function expForLevel(level) {
  return Math.floor(50 * Math.pow(1.15, level - 1));
}

function computeLevel(totalExp) {
  let level = 1;
  let remaining = Math.max(0, totalExp || 0);

  while (remaining >= expForLevel(level) && level < 200) {
    remaining -= expForLevel(level);
    level += 1;
  }

  const currentReq = expForLevel(level);
  const progress = currentReq === 0 ? 0 : remaining / currentReq;

  return {
    level,
    expIntoLevel: remaining,
    expNeeded: currentReq,
    progress: Math.max(0, Math.min(1, progress)),
  };
}

export default function Progress() {
  const { getToken } = useAuth();
  const [me, setMe] = useState(null);
  const [techStats, setTechStats] = useState({}); // { [id]: stats }
  const [error, setError] = useState(null);

  const levelInfo = useMemo(
    () => computeLevel(me?.total_exp || 0),
    [me?.total_exp]
  );

  useEffect(() => {
    const load = async () => {
      setError(null);
      try {
        const token = await getToken();
        if (!token) return;

        // Load global user stats
        const meRes = await fetch("http://localhost:8000/api/me", {
          headers: { Authorization: `Bearer ${token}` },
        });
        const meData = await meRes.json();
        if (!meRes.ok) throw new Error(meData?.detail || "Failed to load /api/me");
        setMe(meData);

        // Load technique stats (all techniques)
        const results = await Promise.all(
          TECHNIQUES.map(async (t) => {
            const res = await fetch(
              `http://localhost:8000/api/technique/${t.id}`,
              { headers: { Authorization: `Bearer ${token}` } }
            );
            const data = await res.json();
            if (!res.ok) throw new Error(data?.detail || `Failed to load ${t.id}`);
            return [t.id, data];
          })
        );

        setTechStats(Object.fromEntries(results));
      } catch (e) {
        setError(String(e.message || e));
      }
    };

    load();
  }, [getToken]);

  return (
    <div className="progress-page">
      <h1 className="progress-title">Progress</h1>

      <SignedOut>
        <div className="progress-card">
          <p className="progress-muted">Please sign in to see your progress.</p>
        </div>
      </SignedOut>

      <SignedIn>
        {error && <div className="progress-error">{error}</div>}

        {/* Overall card */}
        <div className="progress-grid">
          <div className="progress-card">
            <div className="progress-card-header">
              <h2>Overall</h2>
              <span className="chip">Level {levelInfo.level}</span>
            </div>

            <div className="exp-bar">
              <div
                className="exp-bar-fill"
                style={{ width: `${Math.round(levelInfo.progress * 100)}%` }}
              />
            </div>
            <div className="exp-bar-label">
              <span>
                {levelInfo.expIntoLevel} / {levelInfo.expNeeded} EXP to next level
              </span>
              <span>{Math.round(levelInfo.progress * 100)}%</span>
            </div>

            <div className="stats-row">
              <div className="stat">
                <div className="stat-label">Total EXP</div>
                <div className="stat-value">{me?.total_exp ?? 0}</div>
              </div>
              <div className="stat">
                <div className="stat-label">Streak</div>
                <div className="stat-value">{me?.streak ?? 0}</div>
              </div>
              <div className="stat">
                <div className="stat-label">Best Daily</div>
                <div className="stat-value">{me?.best_daily_score ?? 0}</div>
              </div>
            </div>

            <div className="progress-sub">
              <span className="progress-muted">
                Last active: {me?.last_active_date || "—"}
              </span>
            </div>
          </div>

          {/* Technique cards */}
          {TECHNIQUES.map((t) => {
            const tech = techStats[t.id];
            return (
              <div className="progress-card" key={t.id}>
                <div className="progress-card-header">
                  <h2>Technique</h2>
                  <span className="chip">{t.label}</span>
                </div>

                <div className="stats-row">
                  <div className="stat">
                    <div className="stat-label">Technique EXP</div>
                    <div className="stat-value">{tech?.total_exp ?? 0}</div>
                  </div>
                  <div className="stat">
                    <div className="stat-label">Best Score</div>
                    <div className="stat-value">{tech?.best_score ?? 0}</div>
                  </div>
                  <div className="stat">
                    <div className="stat-label">Attempts</div>
                    <div className="stat-value">{tech?.attempts ?? 0}</div>
                  </div>
                </div>

                <div className="progress-note">{t.tip}</div>
              </div>
            );
          })}
        </div>
      </SignedIn>
    </div>
  );
}