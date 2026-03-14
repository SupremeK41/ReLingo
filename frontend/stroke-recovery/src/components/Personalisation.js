// frontend/src/components/Personalisation.js
import React, { useEffect, useMemo, useState } from "react";
import { SignedIn, SignedOut, useAuth } from "@clerk/clerk-react";
import "../App.css";

const API_BASE = "http://localhost:8000";

const TECHNIQUES = [
  { id: "image_naming", label: "Image Naming" },
  { id: "naming_with_cues", label: "Naming with Cues" },
  { id: "sentence_repetition", label: "Sentence Repetition" },
  { id: "semantic_feature_analysis", label: "Semantic Feature Analysis" },
];

const IMAGE_GENERATION_OPTIONS = [
  {
    id: "none",
    label: "Do not generate new images",
    description: "Use the therapy image library only.",
  },
  {
    id: "openai",
    label: "Online image generation",
    description: "Create new images using the cloud model when needed.",
  },
  {
    id: "local",
    label: "Local image generation",
    description: "Create new images on this machine when needed.",
  },
];

export default function Personalisation() {
  const { getToken } = useAuth();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);

  const [error, setError] = useState(null);
  const [status, setStatus] = useState("");

  const [form, setForm] = useState({
    interests_text: "",
    avoid_topics_text: "",
    difficulty: 1,
    sentence_length: 1,
    cue_enabled: true,
    allow_generated_images: false,
    image_generation_mode: "none",
  });

  const [selectedTechnique, setSelectedTechnique] = useState("image_naming");
  const [lastPackSummary, setLastPackSummary] = useState(null);

  const isSentenceTechnique = selectedTechnique === "sentence_repetition";
  const isCueTechnique = selectedTechnique === "naming_with_cues";

  const effectiveImageGenerationMode = useMemo(() => {
    if (!form.allow_generated_images) return "none";
    return form.image_generation_mode || "openai";
  }, [form.allow_generated_images, form.image_generation_mode]);

  const selectedTechniqueLabel =
    TECHNIQUES.find((t) => t.id === selectedTechnique)?.label || "Pack";

  const update = (key, value) => {
    setForm((f) => ({ ...f, [key]: value }));
  };

  const handleAllowGeneratedImagesChange = (checked) => {
    setForm((f) => ({
      ...f,
      allow_generated_images: checked,
      image_generation_mode: checked
        ? (f.image_generation_mode && f.image_generation_mode !== "none"
            ? f.image_generation_mode
            : "openai")
        : "none",
    }));
  };

  const handleGenerationModeChange = (mode) => {
    setForm((f) => ({
      ...f,
      allow_generated_images: mode !== "none",
      image_generation_mode: mode,
    }));
  };

  const loadProfile = async () => {
    setError(null);
    setStatus("");
    setLoading(true);

    try {
      const token = await getToken();
      if (!token) return;

      const res = await fetch(`${API_BASE}/api/personalisation`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Failed to load personalisation");

      const backendAllowGenerated = !!data.allow_generated_images;

      const backendMode =
        typeof data.image_generation_mode === "string" &&
        ["none", "openai", "local"].includes(data.image_generation_mode)
          ? data.image_generation_mode
          : backendAllowGenerated
            ? "openai"
            : "none";

      setForm({
        interests_text: data.interests_text ?? "",
        avoid_topics_text: data.avoid_topics_text ?? "",
        difficulty: Number(data.difficulty ?? 1),
        sentence_length: Number(data.sentence_length ?? 1),
        cue_enabled: !!data.cue_enabled,
        allow_generated_images: backendAllowGenerated,
        image_generation_mode: backendMode,
      });
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadProfile();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveProfile = async ({ silent = false } = {}) => {
    if (!silent) {
      setError(null);
      setStatus("");
    }

    setSaving(true);

    try {
      const token = await getToken();
      if (!token) return false;

      const payload = {
        ...form,
        difficulty: Number(form.difficulty || 1),
        sentence_length: Number(form.sentence_length || 1),
        allow_generated_images: effectiveImageGenerationMode !== "none",
        image_generation_mode: effectiveImageGenerationMode,
      };

      const res = await fetch(`${API_BASE}/api/personalisation`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Failed to save personalisation");

      if (!silent) {
        setStatus("Preferences saved.");
      }

      return true;
    } catch (e) {
      setError(String(e.message || e));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const generatePack = async () => {
    setError(null);
    setStatus("");
    setLastPackSummary(null);
    setGenerating(true);

    try {
      const token = await getToken();
      if (!token) throw new Error("You must be signed in.");

      const saved = await saveProfile({ silent: true });
      if (!saved) {
        throw new Error("Please fix your preferences before generating a pack.");
      }

      const res = await fetch(`${API_BASE}/api/personalisation/generate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          technique_id: selectedTechnique,
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Failed to generate pack");

      setLastPackSummary({
        technique_id: data.technique_id,
        difficulty: data.difficulty,
        count: Array.isArray(data.items) ? data.items.length : 0,
        generated_at: data.generated_at,
        generatedCount: Array.isArray(data.items)
          ? data.items.filter((x) => x?.source === "generated").length
          : 0,
        catalogCount: Array.isArray(data.items)
          ? data.items.filter((x) => x?.source === "catalog").length
          : 0,
      });

      setStatus("Pack ready.");
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setGenerating(false);
    }
  };

  const settingCardStyle = {
    padding: 14,
    borderRadius: 14,
    border: "1px solid rgba(255,255,255,0.12)",
    background: "rgba(0,0,0,0.10)",
  };

  const settingLabelStyle = {
    display: "flex",
    alignItems: "flex-start",
    gap: 12,
    cursor: generating || saving ? "not-allowed" : "pointer",
    width: "100%",
    textAlign: "left",
  };

  const checkboxStyle = {
    marginTop: 4,
    flexShrink: 0,
    width: 18,
    height: 18,
  };

  const generationSummaryText =
    effectiveImageGenerationMode === "local"
      ? "Local image generation selected."
      : effectiveImageGenerationMode === "openai"
        ? "Online image generation selected."
        : "Only curated library images will be used.";

  return (
    <div className="basic-speech-content">
      <h1 className="basic-speech-title">Personalisation</h1>

      <SignedOut>
        <div className="practice-section">
          <p>Please sign in to use personalisation.</p>
        </div>
      </SignedOut>

      <SignedIn>
        <div
          className="practice-section"
          style={{ maxWidth: 900, margin: "0 auto", position: "relative" }}
        >
          <h2 style={{ marginBottom: 6 }}>Make it yours</h2>
          <p style={{ opacity: 0.85, marginTop: 0 }}>
            Tell ReLingo what your daily life looks like, and it will generate practice content that matches your interests.
          </p>

          {error && (
            <div className="error-message" style={{ marginTop: 10 }}>
              {error}
            </div>
          )}

          {status && !generating && (
            <div
              style={{
                marginTop: 10,
                padding: 12,
                borderRadius: 12,
                border: "1px solid rgba(255,255,255,0.15)",
                background: "rgba(0,0,0,0.12)",
                opacity: 0.95,
              }}
            >
              {status}
            </div>
          )}

          {loading ? (
            <p style={{ marginTop: 14 }}>Loading…</p>
          ) : (
            <>
              <div style={{ marginTop: 16 }}>
                <label style={{ fontWeight: 700 }}>Your interests / routine</label>
                <p style={{ marginTop: 4, opacity: 0.8 }}>
                  Example: gym, football, university, coding, barbering, cooking, family, travel.
                </p>
                <textarea
                  value={form.interests_text}
                  onChange={(e) => update("interests_text", e.target.value)}
                  rows={4}
                  style={{
                    width: "100%",
                    borderRadius: 12,
                    padding: 12,
                    border: "1px solid rgba(255,255,255,0.15)",
                    background: "rgba(0,0,0,0.15)",
                    color: "inherit",
                    resize: "vertical",
                  }}
                />
              </div>

              <div style={{ marginTop: 16 }}>
                <label style={{ fontWeight: 700 }}>Avoid topics</label>
                <p style={{ marginTop: 4, opacity: 0.8 }}>
                  Optional: things you do not want included in your exercises.
                </p>
                <input
                  value={form.avoid_topics_text || ""}
                  onChange={(e) => update("avoid_topics_text", e.target.value)}
                  placeholder="e.g. politics, medical details"
                  style={{
                    width: "100%",
                    borderRadius: 12,
                    padding: 12,
                    border: "1px solid rgba(255,255,255,0.15)",
                    background: "rgba(0,0,0,0.15)",
                    color: "inherit",
                  }}
                />
              </div>

              <div style={{ marginTop: 20 }}>
                <label style={{ fontWeight: 700 }}>Exercise type</label>
                <select
                  value={selectedTechnique}
                  onChange={(e) => setSelectedTechnique(e.target.value)}
                  style={{
                    width: "100%",
                    marginTop: 6,
                    borderRadius: 12,
                    padding: 10,
                    border: "1px solid rgba(255,255,255,0.15)",
                    background: "rgba(0,0,0,0.15)",
                    color: "inherit",
                  }}
                >
                  {TECHNIQUES.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>

              <div
                style={{
                  marginTop: 18,
                  display: "grid",
                  gridTemplateColumns: isSentenceTechnique ? "1fr 1fr" : "1fr",
                  gap: 14,
                }}
              >
                <div>
                  <label style={{ fontWeight: 700 }}>Difficulty</label>
                  <select
                    value={form.difficulty}
                    onChange={(e) => update("difficulty", Number(e.target.value))}
                    style={{
                      width: "100%",
                      marginTop: 6,
                      borderRadius: 12,
                      padding: 10,
                      border: "1px solid rgba(255,255,255,0.15)",
                      background: "rgba(0,0,0,0.15)",
                      color: "inherit",
                    }}
                  >
                    <option value={1}>1 — Easy</option>
                    <option value={2}>2 — Medium</option>
                    <option value={3}>3 — Hard</option>
                  </select>
                </div>

                {isSentenceTechnique && (
                  <div>
                    <label style={{ fontWeight: 700 }}>Sentence length</label>
                    <select
                      value={form.sentence_length}
                      onChange={(e) => update("sentence_length", Number(e.target.value))}
                      style={{
                        width: "100%",
                        marginTop: 6,
                        borderRadius: 12,
                        padding: 10,
                        border: "1px solid rgba(255,255,255,0.15)",
                        background: "rgba(0,0,0,0.15)",
                        color: "inherit",
                      }}
                    >
                      <option value={1}>1 — Short</option>
                      <option value={2}>2 — Medium</option>
                      <option value={3}>3 — Long</option>
                    </select>
                  </div>
                )}
              </div>

              <div style={{ marginTop: 18, display: "grid", gap: 12 }}>
                <div style={settingCardStyle}>
                  <label htmlFor="cue-enabled" style={settingLabelStyle}>
                    <input
                      id="cue-enabled"
                      type="checkbox"
                      checked={form.cue_enabled}
                      onChange={(e) => update("cue_enabled", e.target.checked)}
                      disabled={saving || generating}
                      style={checkboxStyle}
                    />
                    <div style={{ minWidth: 0, textAlign: "left" }}>
                      <div style={{ fontWeight: 700 }}>Enable cues</div>
                      <div style={{ opacity: 0.8, marginTop: 4, lineHeight: 1.5 }}>
                        {isCueTechnique
                          ? "Allow ReLingo to build exercises that use semantic and phonemic cueing."
                          : "Show a hint after an incorrect attempt."}
                      </div>
                    </div>
                  </label>
                </div>

                <div style={settingCardStyle}>
                  <label htmlFor="allow-generated-images" style={settingLabelStyle}>
                    <input
                      id="allow-generated-images"
                      type="checkbox"
                      checked={form.allow_generated_images}
                      onChange={(e) => handleAllowGeneratedImagesChange(e.target.checked)}
                      disabled={saving || generating}
                      style={checkboxStyle}
                    />
                    <div style={{ minWidth: 0, textAlign: "left" }}>
                      <div style={{ fontWeight: 700 }}>Allow generated images</div>
                      <div style={{ opacity: 0.8, marginTop: 4, lineHeight: 1.5 }}>
                        If ReLingo cannot find an accurate image in the therapy library, it may create a new one for your personalised exercise.
                      </div>
                    </div>
                  </label>
                </div>

                <div style={settingCardStyle}>
                  <div style={{ fontWeight: 700, textAlign: "left", marginBottom: 10 }}>
                    Image generation method
                  </div>

                  <div style={{ display: "grid", gap: 10 }}>
                    {IMAGE_GENERATION_OPTIONS.map((option) => {
                      const checked = effectiveImageGenerationMode === option.id;

                      return (
                        <label
                          key={option.id}
                          htmlFor={`image-generation-${option.id}`}
                          style={{
                            display: "flex",
                            alignItems: "flex-start",
                            gap: 12,
                            cursor: saving || generating ? "not-allowed" : "pointer",
                            width: "100%",
                            textAlign: "left",
                            padding: 10,
                            borderRadius: 12,
                            border: checked
                              ? "1px solid rgba(255,255,255,0.28)"
                              : "1px solid rgba(255,255,255,0.08)",
                            background: checked
                              ? "rgba(255,255,255,0.06)"
                              : "rgba(255,255,255,0.02)",
                          }}
                        >
                          <input
                            id={`image-generation-${option.id}`}
                            type="radio"
                            name="image_generation_mode"
                            checked={checked}
                            onChange={() => handleGenerationModeChange(option.id)}
                            disabled={saving || generating}
                            style={{ marginTop: 4 }}
                          />
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 700 }}>{option.label}</div>
                            <div style={{ opacity: 0.78, marginTop: 4, lineHeight: 1.5 }}>
                              {option.description}
                            </div>
                          </div>
                        </label>
                      );
                    })}
                  </div>

                  <div style={{ marginTop: 10, opacity: 0.75, textAlign: "left", lineHeight: 1.5 }}>
                    {generationSummaryText}
                  </div>
                </div>
              </div>

              <div style={{ marginTop: 24, display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button
                  onClick={() => saveProfile()}
                  disabled={saving || generating}
                  style={{
                    padding: "10px 16px",
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.15)",
                    cursor: saving || generating ? "not-allowed" : "pointer",
                  }}
                  type="button"
                >
                  {saving ? "Saving..." : "Save Preferences"}
                </button>

                <button
                  onClick={generatePack}
                  disabled={generating || saving}
                  style={{
                    padding: "10px 16px",
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.15)",
                    cursor: generating || saving ? "not-allowed" : "pointer",
                    fontWeight: 700,
                  }}
                  type="button"
                >
                  {generating ? "Generating..." : `Generate ${selectedTechniqueLabel}`}
                </button>
              </div>

              {lastPackSummary && !generating && (
                <div
                  style={{
                    marginTop: 18,
                    padding: 14,
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.15)",
                    background: "rgba(0,0,0,0.12)",
                  }}
                >
                  <div style={{ fontWeight: 800, marginBottom: 8 }}>Latest pack</div>

                  <div style={{ opacity: 0.95 }}>
                    Technique: <strong>{lastPackSummary.technique_id}</strong>
                  </div>
                  <div style={{ opacity: 0.95, marginTop: 4 }}>
                    Difficulty: <strong>{lastPackSummary.difficulty}</strong>
                  </div>
                  <div style={{ opacity: 0.95, marginTop: 4 }}>
                    Items: <strong>{lastPackSummary.count}</strong>
                  </div>

                  <div style={{ opacity: 0.85, marginTop: 8 }}>
                    Curated images: <strong>{lastPackSummary.catalogCount}</strong>
                    <span style={{ marginLeft: 14 }}>
                      Generated images: <strong>{lastPackSummary.generatedCount}</strong>
                    </span>
                  </div>

                  <div style={{ opacity: 0.75, marginTop: 8 }}>
                    Generation method: <strong>{effectiveImageGenerationMode}</strong>
                  </div>

                  <div style={{ opacity: 0.7, marginTop: 8 }}>
                    Generated: {lastPackSummary.generated_at}
                  </div>
                </div>
              )}
            </>
          )}

          {generating && (
            <div
              style={{
                position: "fixed",
                inset: 0,
                background: "rgba(0, 0, 0, 0.7)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                zIndex: 9999,
                padding: 20,
              }}
            >
              <div
                style={{
                  width: "100%",
                  maxWidth: 560,
                  borderRadius: 18,
                  padding: 28,
                  border: "1px solid rgba(255,255,255,0.12)",
                  background: "rgba(18,18,18,0.98)",
                  boxShadow: "0 20px 60px rgba(0,0,0,0.45)",
                  textAlign: "center",
                  color: "#f5f7fb",
                }}
              >
                <div
                  style={{
                    fontSize: 22,
                    fontWeight: 800,
                    marginBottom: 12,
                    color: "#ffffff",
                  }}
                >
                  Generating personalised pack
                </div>

                <div
                  style={{
                    color: "rgba(255,255,255,0.92)",
                    lineHeight: 1.65,
                    fontSize: 16,
                  }}
                >
                  Please stay on this page while ReLingo prepares your therapy content.
                </div>

                <div
                  style={{
                    color: "rgba(255,255,255,0.82)",
                    marginTop: 12,
                    lineHeight: 1.6,
                    fontSize: 15,
                  }}
                >
                  ReLingo is selecting personalised exercises, checking the therapy image library,
                  and creating new images when needed.
                </div>

                <div
                  style={{
                    color: "rgba(255,255,255,0.72)",
                    marginTop: 12,
                    lineHeight: 1.6,
                    fontSize: 15,
                  }}
                >
                  Selected method:{" "}
                  <strong style={{ color: "#ffffff" }}>
                    {effectiveImageGenerationMode === "local"
                      ? "Local image generation"
                      : effectiveImageGenerationMode === "openai"
                        ? "Online image generation"
                        : "Curated library only"}
                  </strong>
                </div>

                <div
                  style={{
                    color: "rgba(255,255,255,0.72)",
                    marginTop: 12,
                    lineHeight: 1.6,
                    fontSize: 15,
                  }}
                >
                  This may take a little longer for highly personalised content.
                </div>

                <div
                  style={{
                    marginTop: 20,
                    fontWeight: 700,
                    color: "#ffffff",
                  }}
                >
                  {selectedTechniqueLabel}
                </div>

                <div style={{ marginTop: 20 }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 18,
                      height: 18,
                      borderRadius: "50%",
                      border: "2px solid rgba(255,255,255,0.22)",
                      borderTopColor: "#ffffff",
                      animation: "spin 1s linear infinite",
                    }}
                  />
                </div>
              </div>

              <style>
                {`
                  @keyframes spin {
                    from { transform: rotate(0deg); }
                    to { transform: rotate(360deg); }
                  }
                `}
              </style>
            </div>
          )}
        </div>
      </SignedIn>
    </div>
  );
}