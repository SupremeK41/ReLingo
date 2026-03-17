// frontend/src/components/Progress.js
import React, { useEffect, useMemo, useState, useCallback } from "react";
import { SignedIn, SignedOut, useAuth } from "@clerk/clerk-react";
import "./Progress.css";

const TECHNIQUES = [
  {
    id: "image_naming",
    label: "Image Naming",
    shortLabel: "Image Naming",
    tip: "Pause, breathe, then try again slowly. Clean naming matters more than rushing.",
  },
  {
    id: "naming_with_cues",
    label: "Naming with Cues",
    shortLabel: "With Cues",
    tip: "Use the cue as support, then aim to retrieve the word more independently over time.",
  },
  {
    id: "sentence_repetition",
    label: "Sentence Repetition",
    shortLabel: "Sentence Repetition",
    tip: "Focus on the key content words first, then add the small connecting words.",
  },
  {
    id: "semantic_feature_analysis",
    label: "Semantic Feature Analysis",
    shortLabel: "SFA",
    tip: "Build the meaning step by step. Think about category, function, location, appearance, and related ideas before naming the word.",
  },
  {
    id: "action_verb_naming",
    label: "Action Verb Naming",
    shortLabel: "Action Verbs",
    tip: "Focus on the action happening in the picture. Think about what the person or subject is doing, then say the verb clearly.",
  },
];

const AI_TECHNIQUE_OPTIONS = [
  { id: "overall", label: "Overall" },
  ...TECHNIQUES.map((t) => ({ id: t.id, label: t.label })),
];

const GRAPH_TECHNIQUE_OPTIONS = [
  { id: "overall", label: "Overall" },
  { id: "image_naming", label: "Image Naming" },
  { id: "naming_with_cues", label: "Naming with Cues" },
  { id: "sentence_repetition", label: "Sentence Repetition" },
  { id: "semantic_feature_analysis", label: "Semantic Feature Analysis" },
  { id: "action_verb_naming", label: "Action Verb Naming" },
];

const GRAPH_METRIC_OPTIONS = {
  overall: [
    { id: "points", label: "Points" },
    { id: "sessions", label: "Sessions" },
  ],
  image_naming: [
    { id: "points", label: "Points" },
    { id: "sessions", label: "Sessions" },
    { id: "first_try_accuracy", label: "First Attempt Accuracy" },
    { id: "avg_attempts_per_item", label: "Avg Attempts / Item" },
    { id: "cue_usage_rate", label: "Cue Usage" },
    { id: "total_items", label: "Items Trained" },
  ],
  naming_with_cues: [
    { id: "points", label: "Points" },
    { id: "sessions", label: "Sessions" },
    { id: "first_try_accuracy", label: "First Attempt Accuracy" },
    { id: "avg_attempts_per_item", label: "Avg Attempts / Item" },
    { id: "cue_usage_rate", label: "Cue Usage" },
    { id: "total_items", label: "Items Trained" },
  ],
  sentence_repetition: [
    { id: "points", label: "Points" },
    { id: "sessions", label: "Sessions" },
    { id: "first_try_accuracy", label: "First Attempt Accuracy" },
    { id: "avg_attempts_per_item", label: "Avg Attempts / Item" },
    { id: "sentence_accuracy", label: "Sentence Accuracy" },
    { id: "total_items", label: "Items Trained" },
  ],
  semantic_feature_analysis: [
    { id: "points", label: "Points" },
    { id: "sessions", label: "Sessions" },
    { id: "first_try_accuracy", label: "First Attempt Accuracy" },
    { id: "avg_attempts_per_item", label: "Avg Attempts / Item" },
    { id: "total_items", label: "Items Trained" },
  ],
  action_verb_naming: [
    { id: "points", label: "Points" },
    { id: "sessions", label: "Sessions" },
    { id: "first_try_accuracy", label: "First Attempt Accuracy" },
    { id: "avg_attempts_per_item", label: "Avg Attempts / Item" },
    { id: "total_items", label: "Items Trained" },
  ],
};

const EMPTY_AI_EVALUATION = {
  target: "overall",
  target_label: "Overall",
  summary: "",
  strengths: [],
  areas_to_strengthen: [],
  technique_observations: [],
  next_focus: [],
  encouraging_note: "",
};

const OVERALL_SECTION_FALLBACKS = {
  strengths: [
    "Regular practice is helping build consistency over time.",
    "Recent sessions are providing useful insight into developing skills.",
  ],
  areas_to_strengthen: [
    "Some responses may benefit from slower, more deliberate retrieval.",
    "A few items may need additional repetition to build confidence.",
  ],
  technique_observations: [
    "Patterns in recent sessions suggest steady improvement through repeated practice.",
    "Some exercises appear easier than others, which is normal during recovery.",
  ],
  next_focus: [
    "Continue short, regular sessions to reinforce retrieval pathways.",
    "Revisit more challenging items while keeping the pace calm and supported.",
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
    first_try_accuracy: "First Attempt Accuracy",
    avg_attempts_per_item: "Avg Attempts / Item",
    sentence_accuracy: "Sentence Accuracy",
    cue_usage_rate: "Cue Usage",
    total_items: "Items Trained",
  };
  return map[metricId] || metricId;
}

function formatMetricValue(metricId, value) {
  if (
    metricId === "first_try_accuracy" ||
    metricId === "sentence_accuracy" ||
    metricId === "cue_usage_rate"
  ) {
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

function sectionTitleForTechnique(techniqueId) {
  return TECHNIQUES.find((t) => t.id === techniqueId)?.label || techniqueId;
}

function shortTechniqueLabel(techniqueId) {
  return TECHNIQUES.find((t) => t.id === techniqueId)?.shortLabel || techniqueId;
}

function aiTargetLabel(techniqueId) {
  return AI_TECHNIQUE_OPTIONS.find((t) => t.id === techniqueId)?.label || techniqueId;
}

function CompactStat({ label, value }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

function EvaluationSection({ title, items = [], emptyText = "No insight yet." }) {
  return (
    <div
      style={{
        padding: 16,
        borderRadius: 16,
        background: "rgba(248,250,252,0.9)",
        border: "1px solid rgba(0,0,0,0.05)",
      }}
    >
      <div style={{ fontWeight: 800, fontSize: 15, marginBottom: 10 }}>{title}</div>
      {!items.length ? (
        <div style={{ fontSize: 14, opacity: 0.65 }}>{emptyText}</div>
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          {items.map((item, idx) => (
            <div
              key={`${title}-${idx}`}
              style={{
                fontSize: 14,
                lineHeight: 1.5,
                display: "flex",
                gap: 8,
                alignItems: "flex-start",
              }}
            >
              <span style={{ fontWeight: 800, opacity: 0.7 }}>•</span>
              <span>{item}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AIProgressEvaluationCard({
  selectedAiTechnique,
  setSelectedAiTechnique,
  aiEvaluation,
  aiLoading,
  aiError,
  onRefresh,
}) {
  const isOverall = selectedAiTechnique === "overall";

  const strengths =
    aiEvaluation?.strengths?.length > 0
      ? aiEvaluation.strengths
      : isOverall
      ? OVERALL_SECTION_FALLBACKS.strengths
      : [];

  const areasToStrengthen =
    aiEvaluation?.areas_to_strengthen?.length > 0
      ? aiEvaluation.areas_to_strengthen
      : isOverall
      ? OVERALL_SECTION_FALLBACKS.areas_to_strengthen
      : [];

  const techniqueObservations =
    aiEvaluation?.technique_observations?.length > 0
      ? aiEvaluation.technique_observations
      : isOverall
      ? OVERALL_SECTION_FALLBACKS.technique_observations
      : [];

  const nextFocus =
    aiEvaluation?.next_focus?.length > 0
      ? aiEvaluation.next_focus
      : isOverall
      ? OVERALL_SECTION_FALLBACKS.next_focus
      : [];

  return (
    <div className="progress-card">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div>
          <div className="progress-card-header" style={{ marginBottom: 0 }}>
            <h2>AI Progress Evaluation</h2>
            <span className="chip">Supportive insight</span>
          </div>
          <div className="progress-muted" style={{ marginTop: 4 }}>
            A supportive summary of progress, areas to strengthen, and helpful next steps.
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "end", flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 220 }}>
            <label className="progress-muted" style={{ fontSize: 13 }}>
              Focus
            </label>
            <select
              value={selectedAiTechnique}
              onChange={(e) => setSelectedAiTechnique(e.target.value)}
              style={{
                padding: "10px 12px",
                borderRadius: 10,
                border: "1px solid rgba(0,0,0,0.08)",
                background: "white",
                fontFamily: "inherit",
              }}
            >
              {AI_TECHNIQUE_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            onClick={onRefresh}
            disabled={aiLoading}
            style={{
              padding: "10px 16px",
              borderRadius: 12,
              border: "1px solid rgba(0,0,0,0.08)",
              background: aiLoading ? "rgba(255,255,255,0.7)" : "#ffffff",
              cursor: aiLoading ? "default" : "pointer",
              fontWeight: 700,
              fontFamily: "inherit",
              minHeight: 42,
            }}
          >
            {aiLoading ? "Refreshing..." : "Refresh Insight"}
          </button>
        </div>
      </div>

      {aiError ? (
        <div className="progress-error" style={{ marginTop: 16 }}>
          {aiError}
        </div>
      ) : null}

      {!aiError && aiLoading && !aiEvaluation?.summary ? (
        <div className="progress-note" style={{ marginTop: 16 }}>
          Generating a supportive progress summary...
        </div>
      ) : null}

      {!aiError && (aiEvaluation?.summary || aiEvaluation?.encouraging_note) ? (
        <div style={{ marginTop: 18, display: "grid", gap: 16 }}>
          <div
            style={{
              padding: 18,
              borderRadius: 18,
              background: "rgba(255,255,255,0.72)",
              border: "1px solid rgba(0,0,0,0.06)",
              boxShadow: "0 10px 28px rgba(16,24,40,0.05)",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 10,
                alignItems: "center",
                flexWrap: "wrap",
                marginBottom: 10,
              }}
            >
              <div style={{ fontSize: 16, fontWeight: 800 }}>
                {aiEvaluation?.target_label || aiTargetLabel(selectedAiTechnique)}
              </div>
              <span className="chip">
                {isOverall ? "AI overall summary" : "AI summary"}
              </span>
            </div>

            <div style={{ fontSize: 15, lineHeight: 1.65 }}>
              {aiEvaluation?.summary || "No summary available yet."}
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: 14,
            }}
          >
            <EvaluationSection
              title="Strengths"
              items={strengths}
              emptyText="No strengths highlighted yet."
            />
            <EvaluationSection
              title="Areas to Strengthen"
              items={areasToStrengthen}
              emptyText="No focus areas highlighted yet."
            />
            <EvaluationSection
              title="Technique Observations"
              items={techniqueObservations}
              emptyText="No technique observations yet."
            />
            <EvaluationSection
              title="Suggested Next Focus"
              items={nextFocus}
              emptyText="No next-step suggestions yet."
            />
          </div>

          {aiEvaluation?.encouraging_note ? (
            <div
              style={{
                padding: 16,
                borderRadius: 16,
                background:
                  "linear-gradient(180deg, rgba(137,207,240,0.18) 0%, rgba(76,139,245,0.12) 100%)",
                border: "1px solid rgba(76,139,245,0.12)",
              }}
            >
              <div style={{ fontWeight: 800, fontSize: 15, marginBottom: 8 }}>
                Encouraging Note
              </div>
              <div style={{ fontSize: 14, lineHeight: 1.6 }}>
                {aiEvaluation.encouraging_note}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {!aiError && !aiLoading && !aiEvaluation?.summary && !aiEvaluation?.encouraging_note ? (
        <div className="progress-note" style={{ marginTop: 16 }}>
          Choose a focus and press refresh to generate an AI progress evaluation.
        </div>
      ) : null}
    </div>
  );
}

function ItemMiniList({
  title,
  subtitle,
  items = [],
  emptyText = "No data yet.",
  showTechnique = false,
}) {
  return (
    <div
      style={{
        padding: 18,
        borderRadius: 18,
        background: "rgba(255,255,255,0.7)",
        border: "1px solid rgba(0,0,0,0.06)",
        boxShadow: "0 10px 28px rgba(16,24,40,0.05)",
      }}
    >
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 16, fontWeight: 800 }}>{title}</div>
        {subtitle ? (
          <div style={{ fontSize: 13, opacity: 0.7, marginTop: 4 }}>{subtitle}</div>
        ) : null}
      </div>

      {!items.length ? (
        <div style={{ fontSize: 14, opacity: 0.65 }}>{emptyText}</div>
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          {items.slice(0, 4).map((item) => (
            <div
              key={item.item_key}
              style={{
                display: "grid",
                gridTemplateColumns: item.image ? "48px 1fr auto" : "1fr auto",
                gap: 12,
                alignItems: "center",
                padding: "10px 12px",
                borderRadius: 14,
                background: "rgba(248,250,252,0.95)",
                border: "1px solid rgba(0,0,0,0.05)",
              }}
            >
              {item.image ? (
                <img
                  src={`/${item.image}`}
                  alt={item.item_label}
                  style={{
                    width: 48,
                    height: 48,
                    objectFit: "cover",
                    borderRadius: 12,
                    border: "1px solid rgba(0,0,0,0.06)",
                    background: "#fff",
                  }}
                />
              ) : null}

              <div style={{ minWidth: 0 }}>
                <div
                  title={item.item_label}
                  style={{
                    fontWeight: 700,
                    fontSize: 14,
                    lineHeight: 1.3,
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    maxWidth: "100%",
                  }}
                >
                  {item.item_label}
                </div>

                {showTechnique && item.technique_id ? (
                  <div
                    style={{
                      marginTop: 4,
                      fontSize: 12,
                      fontWeight: 700,
                      opacity: 0.75,
                    }}
                  >
                    {shortTechniqueLabel(item.technique_id)}
                  </div>
                ) : null}

                <div
                  style={{
                    marginTop: 4,
                    fontSize: 12,
                    opacity: 0.7,
                    display: "flex",
                    gap: 10,
                    flexWrap: "wrap",
                  }}
                >
                  <span>Seen: {item.times_seen ?? 0}</span>
                  <span>Incorrect: {item.times_incorrect ?? 0}</span>
                </div>
              </div>

              <div style={{ textAlign: "right", minWidth: 88 }}>
                {item.first_try_rate != null ? (
                  <>
                    <div style={{ fontSize: 12, opacity: 0.65 }}>First attempt</div>
                    <div style={{ fontWeight: 800, fontSize: 14 }}>
                      {percent(item.first_try_rate)}
                    </div>
                  </>
                ) : null}

                {item.avg_sentence_accuracy != null ? (
                  <>
                    <div style={{ fontSize: 12, opacity: 0.65, marginTop: 4 }}>
                      Accuracy
                    </div>
                    <div style={{ fontWeight: 800, fontSize: 14 }}>
                      {percent(item.avg_sentence_accuracy)}
                    </div>
                  </>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TechniqueCard({ technique, stats }) {
  const isSentence = technique.id === "sentence_repetition";
  const showCue = technique.id === "image_naming" || technique.id === "naming_with_cues";
  const isSfa = technique.id === "semantic_feature_analysis";
  const isActionVerb = technique.id === "action_verb_naming";

  return (
    <div className="progress-card">
      <div className="progress-card-header">
        <h2>{technique.label}</h2>
        <span className="chip">Selected Exercise</span>
      </div>

      <div className="stats-row">
        <CompactStat label="EXP" value={stats?.total_exp ?? 0} />
        <CompactStat
          label="Best Session"
          value={stats?.best_session_points ?? stats?.best_score ?? 0}
        />
        <CompactStat
          label="Sessions"
          value={stats?.sessions_played ?? stats?.attempts ?? 0}
        />
      </div>

      <div className="stats-row">
        <CompactStat label="First Attempt" value={percent(stats?.first_try_accuracy)} />
        <CompactStat
          label="Avg Attempts"
          value={Number(stats?.avg_attempts_per_item || 0).toFixed(1)}
        />
        <CompactStat label="Items" value={stats?.total_items ?? 0} />
      </div>

      {showCue ? (
        <div className="stats-row">
          <CompactStat label="Cue Usage" value={percent(stats?.cue_usage_rate)} />
          <CompactStat label="After Cue" value={stats?.correct_after_cue ?? 0} />
          <CompactStat label="Incorrect" value={stats?.incorrect ?? 0} />
        </div>
      ) : null}

      {isSentence ? (
        <div className="stats-row">
          <CompactStat
            label="Sentence Accuracy"
            value={percent(stats?.lifetime_avg_sentence_accuracy)}
          />
          <CompactStat
            label="Best Accuracy"
            value={percent(stats?.best_sentence_accuracy)}
          />
          <CompactStat
            label="Sentence Items"
            value={stats?.sentence_items_evaluated ?? 0}
          />
        </div>
      ) : null}

      {isSfa ? (
        <div className="stats-row">
          <CompactStat label="Correct First" value={stats?.correct_first ?? 0} />
          <CompactStat label="Correct Second" value={stats?.correct_second ?? 0} />
          <CompactStat label="Incorrect" value={stats?.incorrect ?? 0} />
        </div>
      ) : null}

      {isActionVerb ? (
        <div className="stats-row">
          <CompactStat label="Correct First" value={stats?.correct_first ?? 0} />
          <CompactStat label="Correct Second" value={stats?.correct_second ?? 0} />
          <CompactStat label="Incorrect" value={stats?.incorrect ?? 0} />
        </div>
      ) : null}

      <div className="progress-note">{technique.tip}</div>
    </div>
  );
}

function PerTechniqueFocus({ byTechniqueAnalytics }) {
  return (
    <div className="progress-card">
      <div className="progress-card-header">
        <h2>Per-Technique Focus</h2>
        <span className="chip">Breakdown</span>
      </div>

      <div
        style={{
          display: "grid",
          gap: 18,
          marginTop: 10,
        }}
      >
        {TECHNIQUES.map((technique) => {
          const analytics = byTechniqueAnalytics?.[technique.id] || {};

          return (
            <div
              key={technique.id}
              style={{
                paddingTop: 6,
                borderTop: "1px solid rgba(0,0,0,0.06)",
              }}
            >
              <div
                style={{
                  fontWeight: 800,
                  fontSize: 16,
                  marginBottom: 12,
                }}
              >
                {sectionTitleForTechnique(technique.id)}
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                  gap: 14,
                }}
              >
                <ItemMiniList
                  title="Needs More Practice"
                  items={analytics?.hardest_items || []}
                />
                <ItemMiniList
                  title="Most Retried"
                  items={analytics?.most_retried_items || []}
                />
                {(technique.id === "image_naming" ||
                  technique.id === "naming_with_cues") && (
                  <ItemMiniList
                    title="Cue Reliance"
                    items={analytics?.most_cue_dependent_items || []}
                  />
                )}
                {technique.id === "sentence_repetition" && (
                  <ItemMiniList
                    title="Lowest Accuracy Sentences"
                    items={analytics?.weakest_sentence_items || []}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function Progress() {
  const { getToken } = useAuth();

  const [me, setMe] = useState(null);
  const [summary, setSummary] = useState({
    daily_points: [],
    techniques: {},
    technique_history: {},
    item_analytics: {
      hardest_items: [],
      most_retried_items: [],
      most_cue_dependent_items: [],
      weakest_sentence_items: [],
      by_technique: {},
    },
  });
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const [selectedTechnique, setSelectedTechnique] = useState("overall");
  const [selectedMetric, setSelectedMetric] = useState("points");
  const [selectedOverviewTechnique, setSelectedOverviewTechnique] = useState("image_naming");
  const [selectedAiTechnique, setSelectedAiTechnique] = useState("overall");
  const [showPerTechniqueFocus, setShowPerTechniqueFocus] = useState(false);

  const [aiEvaluation, setAiEvaluation] = useState(EMPTY_AI_EVALUATION);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState(null);

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

  const itemAnalytics = summary?.item_analytics || {};
  const byTechniqueAnalytics = itemAnalytics?.by_technique || {};

  const selectedOverviewTechniqueConfig = useMemo(
    () => TECHNIQUES.find((t) => t.id === selectedOverviewTechnique) || TECHNIQUES[0],
    [selectedOverviewTechnique]
  );

  const selectedOverviewStats = useMemo(
    () => summary?.techniques?.[selectedOverviewTechnique] || {},
    [summary, selectedOverviewTechnique]
  );

  const fetchAiEvaluation = useCallback(
    async (techniqueId = selectedAiTechnique) => {
      setAiLoading(true);
      setAiError(null);

      try {
        const token = await getToken();
        if (!token) {
          throw new Error("You need to be signed in to generate an evaluation.");
        }

        const headers = {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        };

        const res = await fetch("http://localhost:8000/api/progress/ai-evaluation", {
          method: "POST",
          headers,
          body: JSON.stringify({
            target: techniqueId,
            summary,
            profile: me,
          }),
        });

        const data = await res.json();

        if (!res.ok) {
          throw new Error(data?.detail || "Failed to generate AI progress evaluation.");
        }

        setAiEvaluation({
          target: data?.target || techniqueId,
          target_label: data?.target_label || aiTargetLabel(techniqueId),
          summary: data?.summary || "",
          strengths: Array.isArray(data?.strengths) ? data.strengths : [],
          areas_to_strengthen: Array.isArray(data?.areas_to_strengthen)
            ? data.areas_to_strengthen
            : [],
          technique_observations: Array.isArray(data?.technique_observations)
            ? data.technique_observations
            : [],
          next_focus: Array.isArray(data?.next_focus) ? data.next_focus : [],
          encouraging_note: data?.encouraging_note || "",
        });
      } catch (e) {
        setAiError(String(e.message || e));
      } finally {
        setAiLoading(false);
      }
    },
    [getToken, me, selectedAiTechnique, summary]
  );

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

  useEffect(() => {
    if (!loading && me && summary) {
      fetchAiEvaluation(selectedAiTechnique);
    }
  }, [loading, me, summary, selectedAiTechnique, fetchAiEvaluation]);

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
          <div
            style={{
              display: "grid",
              gap: 20,
            }}
          >
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
                  <CompactStat label="Total EXP" value={me?.total_exp ?? 0} />
                  <CompactStat label="Streak" value={me?.streak ?? 0} />
                  <CompactStat label="Best Daily" value={me?.best_daily_score ?? 0} />
                </div>

                <div className="stats-row">
                  <CompactStat label="Today Points" value={me?.today_points ?? 0} />
                  <CompactStat label="Today Sessions" value={me?.today_sessions ?? 0} />
                  <CompactStat label="Last Active" value={me?.last_active_date || "—"} />
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
                    Use this to quickly compare your recent activity and spot changes in performance.
                  </div>
                </div>
              </div>
            </div>

            <AIProgressEvaluationCard
              selectedAiTechnique={selectedAiTechnique}
              setSelectedAiTechnique={setSelectedAiTechnique}
              aiEvaluation={aiEvaluation}
              aiLoading={aiLoading}
              aiError={aiError}
              onRefresh={() => fetchAiEvaluation(selectedAiTechnique)}
            />

            <div className="progress-card">
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 12,
                  flexWrap: "wrap",
                }}
              >
                <div>
                  <div className="progress-card-header" style={{ marginBottom: 0 }}>
                    <h2>Technique Overview</h2>
                    <span className="chip">Focused view</span>
                  </div>
                  <div className="progress-muted" style={{ marginTop: 4 }}>
                    View one exercise at a time for a cleaner summary.
                  </div>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 220 }}>
                  <label className="progress-muted" style={{ fontSize: 13 }}>
                    Exercise
                  </label>
                  <select
                    value={selectedOverviewTechnique}
                    onChange={(e) => setSelectedOverviewTechnique(e.target.value)}
                    style={{
                      padding: "10px 12px",
                      borderRadius: 10,
                      border: "1px solid rgba(0,0,0,0.08)",
                      background: "white",
                      fontFamily: "inherit",
                    }}
                  >
                    {TECHNIQUES.map((technique) => (
                      <option key={technique.id} value={technique.id}>
                        {technique.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div style={{ marginTop: 16 }}>
                <TechniqueCard
                  technique={selectedOverviewTechniqueConfig}
                  stats={selectedOverviewStats}
                />
              </div>
            </div>

            <div className="progress-card">
              <div className="progress-card-header">
                <h2>Practice Focus</h2>
                <span className="chip">Top priorities</span>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
                  gap: 16,
                  marginTop: 8,
                }}
              >
                <ItemMiniList
                  title="Hardest Items"
                  subtitle="Items with the most mistakes so far."
                  items={itemAnalytics?.hardest_items || []}
                  showTechnique={true}
                />
                <ItemMiniList
                  title="Most Retried"
                  subtitle="Items that usually take more attempts."
                  items={itemAnalytics?.most_retried_items || []}
                  showTechnique={true}
                />
              </div>
            </div>

            <div className="progress-card">
              <div className="progress-card-header">
                <h2>More Detail</h2>
                <span className="chip">Optional</span>
              </div>

              <div className="progress-note" style={{ marginBottom: 14 }}>
                Open the detailed breakdown only when you want a closer look at each technique.
              </div>

              <button
                type="button"
                onClick={() => setShowPerTechniqueFocus((prev) => !prev)}
                style={{
                  padding: "10px 16px",
                  borderRadius: 12,
                  border: "1px solid rgba(0,0,0,0.08)",
                  background: "#ffffff",
                  cursor: "pointer",
                  fontWeight: 700,
                  fontFamily: "inherit",
                }}
              >
                {showPerTechniqueFocus ? "Hide Detailed Breakdown" : "Show Detailed Breakdown"}
              </button>
            </div>

            {showPerTechniqueFocus && (
              <PerTechniqueFocus byTechniqueAnalytics={byTechniqueAnalytics} />
            )}
          </div>
        )}
      </SignedIn>
    </div>
  );
}