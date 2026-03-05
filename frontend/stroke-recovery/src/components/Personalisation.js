// frontend/src/components/Personalisation.js
import React, { useEffect, useState } from "react";
import { SignedIn, SignedOut, useAuth } from "@clerk/clerk-react";
import "../App.css";

const API_BASE = "http://localhost:8000";

const TECHNIQUES = [
  { id: "image_naming", label: "Image Naming" },
  { id: "sentence_repetition", label: "Sentence Repetition" },
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
  });

  const [selectedTechnique, setSelectedTechnique] = useState("image_naming");
  const [lastPackSummary, setLastPackSummary] = useState(null);

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

      setForm({
        interests_text: data.interests_text ?? "",
        avoid_topics_text: data.avoid_topics_text ?? "",
        difficulty: Number(data.difficulty ?? 1),
        sentence_length: Number(data.sentence_length ?? 1),
        cue_enabled: !!data.cue_enabled,
        allow_generated_images: !!data.allow_generated_images,
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

  const saveProfile = async () => {
    setError(null);
    setStatus("");
    setSaving(true);

    try {
      const token = await getToken();
      if (!token) return;

      const res = await fetch(`${API_BASE}/api/personalisation`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          ...form,
          difficulty: Number(form.difficulty || 1),
          sentence_length: Number(form.sentence_length || 1),
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Failed to save personalisation");

      setStatus("Saved.");
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  const generatePack = async () => {
    setError(null);
    setStatus("");
    setGenerating(true);
    setLastPackSummary(null);

    try {
      const token = await getToken();
      if (!token) return;

      // Ensure profile saved first (so generation uses latest prefs)
      await saveProfile();

      const res = await fetch(`${API_BASE}/api/personalisation/generate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          technique_id: selectedTechnique,
          // optional override (normally leave null and use saved profile)
          // difficulty: Number(form.difficulty || 1),
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Failed to generate pack");

      setStatus("Generated new pack.");
      setLastPackSummary({
        technique_id: data.technique_id,
        difficulty: data.difficulty,
        count: Array.isArray(data.items) ? data.items.length : 0,
        generated_at: data.generated_at,
      });
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setGenerating(false);
    }
  };

  const update = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  return (
    <div className="basic-speech-content">
      <h1 className="basic-speech-title">Personalisation</h1>

      <SignedOut>
        <div className="practice-section">
          <p>Please sign in to use personalisation.</p>
        </div>
      </SignedOut>

      <SignedIn>
        <div className="practice-section" style={{ maxWidth: 900, margin: "0 auto" }}>
          <h2 style={{ marginBottom: 6 }}>Make it yours</h2>
          <p style={{ opacity: 0.85, marginTop: 0 }}>
            Tell ReLingo what your daily life looks like, and it will generate practice content that matches your interests.
          </p>

          {error && (
            <div className="error-message" style={{ marginTop: 10 }}>
              {error}
            </div>
          )}

          {status && (
            <div
              style={{
                marginTop: 10,
                padding: 10,
                borderRadius: 10,
                border: "1px solid rgba(255,255,255,0.15)",
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
              {/* Interests */}
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

              {/* Avoid topics */}
              <div style={{ marginTop: 16 }}>
                <label style={{ fontWeight: 700 }}>Avoid topics</label>
                <p style={{ marginTop: 4, opacity: 0.8 }}>
                  Optional: things you don’t want included in examples.
                </p>
                <input
                  value={form.avoid_topics_text || ""}
                  onChange={(e) => update("avoid_topics_text", e.target.value)}
                  placeholder="e.g., politics, medical details"
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

              {/* Difficulty */}
              <div style={{ marginTop: 18, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
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
              </div>

              {/* Toggles */}
              <div style={{ marginTop: 18, display: "grid", gap: 10 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <input
                    type="checkbox"
                    checked={form.cue_enabled}
                    onChange={(e) => update("cue_enabled", e.target.checked)}
                  />
                  <span>
                    <strong>Enable cues</strong> (hints after an incorrect attempt)
                  </span>
                </label>

                <label style={{ display: "flex", alignItems: "center", gap: 10, opacity: 0.85 }}>
                  <input
                    type="checkbox"
                    checked={form.allow_generated_images}
                    onChange={(e) => update("allow_generated_images", e.target.checked)}
                    disabled
                  />
                  <span>
                    <strong>Allow generated images</strong> (coming later — currently using curated image catalog)
                  </span>
                </label>
              </div>

              {/* Actions */}
              <div style={{ marginTop: 22, display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button
                  onClick={saveProfile}
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

                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <select
                    value={selectedTechnique}
                    onChange={(e) => setSelectedTechnique(e.target.value)}
                    style={{
                      borderRadius: 12,
                      padding: 10,
                      border: "1px solid rgba(255,255,255,0.15)",
                      background: "rgba(0,0,0,0.15)",
                      color: "inherit",
                    }}
                  >
                    {TECHNIQUES.map((t) => (
                      <option key={t.id} value={t.id}>
                        Generate for: {t.label}
                      </option>
                    ))}
                  </select>

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
                    {generating ? "Generating..." : "Generate Pack"}
                  </button>
                </div>
              </div>

              {lastPackSummary && (
                <div
                  style={{
                    marginTop: 16,
                    padding: 12,
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.15)",
                    background: "rgba(0,0,0,0.12)",
                  }}
                >
                  <div style={{ fontWeight: 800, marginBottom: 6 }}>Latest pack</div>
                  <div style={{ opacity: 0.9 }}>
                    Technique: <strong>{lastPackSummary.technique_id}</strong> • Difficulty:{" "}
                    <strong>{lastPackSummary.difficulty}</strong> • Items:{" "}
                    <strong>{lastPackSummary.count}</strong>
                  </div>
                  <div style={{ opacity: 0.7, marginTop: 4 }}>
                    Generated: {lastPackSummary.generated_at}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </SignedIn>
    </div>
  );
}