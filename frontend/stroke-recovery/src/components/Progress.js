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

const GRAPH_TECHNIQUE_OPTIONS = [
  { id: "overall", label: "Overall" },
  { id: "image_naming", label: "Image Naming" },
  { id: "sentence_repetition", label: "Sentence Repetition" },
];

const GRAPH_METRIC_OPTIONS = {
  overall: [
    { id: "points", label: "Points" },
    { id: "sessions", label: "Sessions" },
  ],
  image_naming: [
    { id: "points", label: "Points" },
    { id: "sessions", label: "Sessions" },
    { id: "first_try_accuracy", label: "First Try Accuracy" },
    { id: "avg_attempts_per_item", label: "Avg Attempts / Item" },
    { id: "total_items", label: "Items Trained" },
  ],
  sentence_repetition: [
    { id: "points", label: "Points" },
    { id: "sessions", label: "Sessions" },
    { id: "first_try_accuracy", label: "First Try Accuracy" },
    { id: "avg_attempts_per_item", label: "Avg Attempts / Item" },
    { id: "sentence_accuracy", label: "Sentence Accuracy" },
    { id: "total_items", label: "Items Trained" },
  ],
};

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

function percent(v) {
  return `${Math.round((v || 0) * 100)}%`;
}

function metricLabel(metricId) {
  const map = {
    points: "Points",
    sessions: "Sessions",
    first_try_accuracy: "First Try Accuracy",
    avg_attempts_per_item: "Avg Attempts / Item",
    sentence_accuracy: "Sentence Accuracy",
    total_items: "Items Trained",
  };
  return map[metricId] || metricId;
}

function formatMetricValue(metricId, value) {
  if (metricId === "first_try_accuracy" || metricId === "sentence_accuracy") {
    return percent(value);
  }
  if (metricId === "avg_attempts_per_item") {
    return Number(value || 0).toFixed(1);
  }
  return String(value || 0);
}

function buildGraphSeries(summary, selectedTechnique, selectedMetric) {
  if (selectedTechnique === "overall") {
    return (summary?.daily_points || []).map((d) => ({
      day: d.day,
      value: selectedMetric === "sessions" ? d.sessions : d.points,
      sessions: d.sessions,
      points: d.points,
    }));
  }

  const techniqueSeries = summary?.technique_history?.[selectedTechnique] || [];
  return techniqueSeries.map((d) => ({
    day: d.day,
    value: d?.[selectedMetric] ?? 0,
    raw: d,
  }));
}

export default function Progress() {
  const { getToken } = useAuth();

  const [me, setMe] = useState(null);
  const [summary, setSummary] = useState({
    daily_points: [],
    techniques: {},
    technique_history: {},
  });
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const [selectedTechnique, setSelectedTechnique] = useState("overall");
  const [selectedMetric, setSelectedMetric] = useState("points");

  const levelInfo = useMemo(
    () => computeLevel(me?.total_exp || 0),
    [me?.total_exp]
  );

  useEffect(() => {
    const allowedMetrics =
      GRAPH_METRIC_OPTIONS[selectedTechnique] || GRAPH_METRIC_OPTIONS.overall;
    if (!allowedMetrics.some((m) => m.id === selectedMetric)) {
      setSelectedMetric(allowedMetrics[0].id);
    }
  }, [selectedTechnique, selectedMetric]);

  const graphSeries = useMemo(
    () => buildGraphSeries(summary, selectedTechnique, selectedMetric),
    [summary, selectedTechnique, selectedMetric]
  );

  const maxGraphValue = useMemo(() => {
    const vals = graphSeries.map((d) => Number(d.value || 0));
    return Math.max(1, ...vals);
  }, [graphSeries]);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError(null);

      try {
        const token = await getToken();
        if (!token) {
          setLoading(false);
          return;
        }

        const headers = { Authorization: `Bearer ${token}` };

        const meRes = await fetch("http://localhost:8000/api/me", { headers });
        const meData = await meRes.json();
        if (!meRes.ok) throw new Error(meData?.detail || "Failed to load /api/me");
        setMe(meData);

        const summaryRes = await fetch("http://localhost:8000/api/progress/summary", {
          headers,
        });
        const summaryData = await summaryRes.json();
        if (!summaryRes.ok) {
          throw new Error(summaryData?.detail || "Failed to load progress summary");
        }
        setSummary(summaryData);
      } catch (e) {
        setError(String(e.message || e));
      } finally {
        setLoading(false);
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

        {loading && (
          <div className="progress-card">
            <p className="progress-muted">Loading progress...</p>
          </div>
        )}

        {!loading && (
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

              <div className="stats-row">
                <div className="stat">
                  <div className="stat-label">Today Points</div>
                  <div className="stat-value">{me?.today_points ?? 0}</div>
                </div>
                <div className="stat">
                  <div className="stat-label">Today Sessions</div>
                  <div className="stat-value">{me?.today_sessions ?? 0}</div>
                </div>
                <div className="stat">
                  <div className="stat-label">Last Active</div>
                  <div className="stat-value">{me?.last_active_date || "—"}</div>
                </div>
              </div>
            </div>

            <div className="progress-card">
              <div className="progress-card-header">
                <h2>7-Day Activity</h2>
                <span className="chip">Growth</span>
              </div>

              <div
                style={{
                  marginTop: 12,
                  display: "flex",
                  gap: 12,
                  flexWrap: "wrap",
                }}
              >
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <label className="progress-muted" style={{ fontSize: 13 }}>
                    Technique
                  </label>
                  <select
                    value={selectedTechnique}
                    onChange={(e) => setSelectedTechnique(e.target.value)}
                    style={{
                      padding: "8px 12px",
                      borderRadius: 10,
                      border: "1px solid rgba(0,0,0,0.08)",
                      background: "white",
                      fontFamily: "inherit",
                    }}
                  >
                    {GRAPH_TECHNIQUE_OPTIONS.map((opt) => (
                      <option key={opt.id} value={opt.id}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <label className="progress-muted" style={{ fontSize: 13 }}>
                    Metric
                  </label>
                  <select
                    value={selectedMetric}
                    onChange={(e) => setSelectedMetric(e.target.value)}
                    style={{
                      padding: "8px 12px",
                      borderRadius: 10,
                      border: "1px solid rgba(0,0,0,0.08)",
                      background: "white",
                      fontFamily: "inherit",
                    }}
                  >
                    {(GRAPH_METRIC_OPTIONS[selectedTechnique] || []).map((opt) => (
                      <option key={opt.id} value={opt.id}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div
                style={{
                  marginTop: 18,
                  padding: "18px 14px 12px",
                  borderRadius: 16,
                  background: "rgba(255,255,255,0.03)",
                  minHeight: 250,
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                }}
              >
                <div
                  style={{
                    marginBottom: 10,
                    fontSize: 14,
                    fontWeight: 700,
                    opacity: 0.9,
                  }}
                >
                  Showing:{" "}
                  {GRAPH_TECHNIQUE_OPTIONS.find((x) => x.id === selectedTechnique)?.label} —{" "}
                  {metricLabel(selectedMetric)}
                </div>

                <div
                  style={{
                    height: 190,
                    display: "grid",
                    gridTemplateColumns: "repeat(7, 1fr)",
                    alignItems: "end",
                    gap: 14,
                  }}
                >
                  {graphSeries.map((d) => {
                    const normalized =
                      maxGraphValue > 0 ? Math.sqrt((d.value || 0) / maxGraphValue) : 0;
                    const barHeight = d.value > 0 ? Math.max(14, normalized * 130) : 10;

                    return (
                      <div
                        key={d.day}
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "center",
                          justifyContent: "end",
                          height: "100%",
                        }}
                        title={`${d.day}: ${formatMetricValue(selectedMetric, d.value)}`}
                      >
                        <div
                          style={{
                            fontSize: 12,
                            marginBottom: 6,
                            opacity: 0.85,
                            fontWeight: 700,
                            textAlign: "center",
                            minHeight: 18,
                          }}
                        >
                          {formatMetricValue(selectedMetric, d.value)}
                        </div>

                        <div
                          style={{
                            height: 140,
                            width: "100%",
                            display: "flex",
                            alignItems: "end",
                            justifyContent: "center",
                          }}
                        >
                          <div
                            style={{
                              width: 30,
                              height: `${barHeight}px`,
                              borderRadius: "10px 10px 4px 4px",
                              background:
                                d.value > 0
                                  ? "linear-gradient(180deg, #89cff0 0%, #4c8bf5 100%)"
                                  : "rgba(255,255,255,0.12)",
                              boxShadow:
                                d.value > 0
                                  ? "0 4px 14px rgba(76, 139, 245, 0.22)"
                                  : "none",
                              transition: "height 0.25s ease",
                            }}
                          />
                        </div>

                        <div
                          style={{
                            marginTop: 8,
                            fontSize: 12,
                            opacity: 0.75,
                          }}
                        >
                          {d.day.slice(5)}
                        </div>
                      </div>
                    );
                  })}
                </div>

                <div className="progress-note" style={{ marginTop: 16 }}>
                  Use the dropdowns to compare points, sessions, first-try accuracy,
                  sentence accuracy, and other useful progress metrics across the last 7 days.
                </div>
              </div>
            </div>

            {TECHNIQUES.map((t) => {
              const tech = summary?.techniques?.[t.id] || {};

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
                      <div className="stat-label">Best Session</div>
                      <div className="stat-value">
                        {tech?.best_session_points ?? tech?.best_score ?? 0}
                      </div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">Sessions</div>
                      <div className="stat-value">
                        {tech?.sessions_played ?? tech?.attempts ?? 0}
                      </div>
                    </div>
                  </div>

                  <div className="stats-row">
                    <div className="stat">
                      <div className="stat-label">First Try Accuracy</div>
                      <div className="stat-value">{percent(tech?.first_try_accuracy)}</div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">Avg Attempts / Item</div>
                      <div className="stat-value">{tech?.avg_attempts_per_item ?? 0}</div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">Items Trained</div>
                      <div className="stat-value">{tech?.total_items ?? 0}</div>
                    </div>
                  </div>

                  {t.id === "sentence_repetition" && (
                    <div className="stats-row">
                      <div className="stat">
                        <div className="stat-label">Sentence Accuracy</div>
                        <div className="stat-value">
                          {percent(tech?.lifetime_avg_sentence_accuracy)}
                        </div>
                      </div>
                      <div className="stat">
                        <div className="stat-label">Best Sentence Accuracy</div>
                        <div className="stat-value">
                          {percent(tech?.best_sentence_accuracy)}
                        </div>
                      </div>
                      <div className="stat">
                        <div className="stat-label">Sentence Items</div>
                        <div className="stat-value">
                          {tech?.sentence_items_evaluated ?? 0}
                        </div>
                      </div>
                    </div>
                  )}

                  <div className="progress-note">{t.tip}</div>
                </div>
              );
            })}
          </div>
        )}
      </SignedIn>
    </div>
  );
}