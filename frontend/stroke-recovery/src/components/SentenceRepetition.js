// frontend/src/components/SentenceRepetition.js
import React, { useEffect, useMemo, useRef, useState } from "react";
import "../App.css";
import "./BasicSpeech.css";

import { useAuth } from "@clerk/clerk-react";
import sentenceData from "../data/sentenceProduction.json";

const SESSION_SIZE = 5;
const TECHNIQUE_ID = "sentence_repetition";
const API_BASE = "http://localhost:8000";

const DEFAULT_FUNCTION_WORDS = new Set([
  "a", "an", "the",
  "is", "am", "are", "was", "were", "be", "being", "been",
  "to", "of", "in", "on", "at", "by", "for", "with", "from",
  "and", "or", "but",
  "will", "would", "can", "could", "should", "shall", "may", "might",
  "do", "does", "did",
  "has", "have", "had",
  "he", "she", "it", "they", "we", "you", "i",
  "him", "her", "them", "us",
  "this", "that", "these", "those",
  "my", "your", "his", "their", "our",
  "as", "if", "than", "then", "so", "because", "while",
]);

function resolveImagePath(image) {
  if (!image) return "/placeholder.jpg";
  if (image.startsWith("/")) return image;

  if (
    image.startsWith("images/") ||
    image.startsWith("SentenceImages/") ||
    image.startsWith("arasaac_images/") ||
    image.startsWith("arasaac_sentence_images/") ||
    image.startsWith("generated/")
  ) {
    return `/${image}`;
  }

  return `/images/${image}`;
}

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function pointsForAttempt(attemptNum) {
  if (attemptNum === 1) return 3;
  if (attemptNum === 2) return 2;
  return 1;
}

function isCorrect(scoreLevel) {
  return Number(scoreLevel) >= 2;
}

function normaliseSentenceText(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^\w\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function deriveSentenceTargets(sentence) {
  const norm = normaliseSentenceText(sentence);
  const tokens = norm.split(" ").filter(Boolean);

  const required_function = tokens.filter((t) => DEFAULT_FUNCTION_WORDS.has(t));
  const required_content = tokens.filter((t) => !DEFAULT_FUNCTION_WORDS.has(t));

  return {
    required_content: [...new Set(required_content)],
    required_function: [...new Set(required_function)],
  };
}

function normaliseSentenceItem(it) {
  const sentence = String(it?.sentence || it?.text || "").trim();
  const image = String(it?.image || "").trim();

  const derivedTargets = deriveSentenceTargets(sentence);

  return {
    sentence,
    image,
    required_content:
      Array.isArray(it?.required_content) && it.required_content.length > 0
        ? it.required_content
            .map((x) => String(x || "").trim())
            .filter(Boolean)
        : derivedTargets.required_content,
    required_function:
      Array.isArray(it?.required_function) && it.required_function.length > 0
        ? it.required_function
            .map((x) => String(x || "").trim())
            .filter(Boolean)
        : derivedTargets.required_function,
  };
}

function getDefaultSentenceItems() {
  return Array.isArray(sentenceData)
    ? sentenceData.map(normaliseSentenceItem).filter((it) => it.sentence)
    : [];
}

export default function SentenceRepetition() {
  const { getToken } = useAuth();

  const [contentSource, setContentSource] = useState("local");
  const [baseItems, setBaseItems] = useState(getDefaultSentenceItems());
  const [packError, setPackError] = useState(null);

  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [lockedUntilNext, setLockedUntilNext] = useState(false);

  const [currentIndex, setCurrentIndex] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [audioResult, setAudioResult] = useState("");

  const [attemptNum, setAttemptNum] = useState(1);
  const [sessionPoints, setSessionPoints] = useState(0);
  const [completed, setCompleted] = useState(false);

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);

  const [sessionSeed, setSessionSeed] = useState(0);

  const postedSessionRef = useRef(false);
  const [scorePostResult, setScorePostResult] = useState(null);
  const [scorePostError, setScorePostError] = useState(null);

  const [itemResults, setItemResults] = useState([]);

  const [isMuted, setIsMuted] = useState(() => {
    try {
      return localStorage.getItem("rel_mute_sentence") === "1";
    } catch {
      return false;
    }
  });
  const lastSpokenRef = useRef("");

  useEffect(() => {
    const loadPack = async () => {
      setPackError(null);

      try {
        const token = await getToken();
        if (!token) {
          setContentSource("local");
          setBaseItems(getDefaultSentenceItems());
          return;
        }

        const res = await fetch(`${API_BASE}/api/content-pack/active/${TECHNIQUE_ID}`, {
          headers: { Authorization: `Bearer ${token}` },
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || "Failed to load content pack");

        const items = Array.isArray(data?.items) ? data.items : [];
        const cleaned = items.map(normaliseSentenceItem).filter((it) => it.sentence);

        if (cleaned.length > 0) {
          setContentSource("pack");
          setBaseItems(cleaned);
        } else {
          setContentSource("local");
          setBaseItems(getDefaultSentenceItems());
        }
      } catch (e) {
        setPackError(String(e.message || e));
        setContentSource("local");
        setBaseItems(getDefaultSentenceItems());
      }
    };

    loadPack();
  }, [getToken]);

  const sessionItems = useMemo(() => {
    const shuffled = shuffle(baseItems || []);
    return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
  }, [sessionSeed, baseItems]);

  const currentItem = sessionItems[currentIndex];

  const speak = (text) => {
    if (isMuted) return;
    if (!text) return;
    if (!("speechSynthesis" in window)) return;
    if (lastSpokenRef.current === text) return;

    lastSpokenRef.current = text;

    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 0.9;
    window.speechSynthesis.speak(u);
  };

  useEffect(() => {
    if (feedback) speak(feedback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedback]);

  const toggleMute = () => {
    setIsMuted((m) => {
      const next = !m;
      try {
        localStorage.setItem("rel_mute_sentence", next ? "1" : "0");
      } catch {}
      if (next) window.speechSynthesis.cancel();
      return next;
    });
  };

  useEffect(() => {
    setCurrentIndex(0);
    setFeedback("");
    setAudioResult("");
    setAttemptNum(1);
    setSessionPoints(0);
    setCompleted(false);
    setLockedUntilNext(false);
    setIsRecording(false);
    setIsProcessing(false);
    lastSpokenRef.current = "";

    postedSessionRef.current = false;
    setScorePostResult(null);
    setScorePostError(null);
    setItemResults([]);
  }, [sessionItems]);

  const nextItem = () => {
    const next = currentIndex + 1;
    if (next < sessionItems.length) {
      setCurrentIndex(next);
      setAttemptNum(1);
      setFeedback("");
      setAudioResult("");
      setLockedUntilNext(false);
    } else {
      setCompleted(true);
      setFeedback("");
      setAudioResult("");
      setLockedUntilNext(false);
    }
  };

  const finalizeItem = ({ awardedPoints, payload, scoreLevel }) => {
    const targetSentence = currentItem?.sentence || "";

    setSessionPoints((p) => p + awardedPoints);

    setItemResults((prev) => [
      ...prev,
      {
        target: targetSentence,
        awardedPoints,
        attemptsUsed: attemptNum,
        cueShown: false,
        transcript: payload?.text || "",
        best_guess: null,
        matched: isCorrect(scoreLevel),
        sentence_accuracy:
          typeof payload?.weighted_accuracy === "number"
            ? payload.weighted_accuracy
            : null,
      },
    ]);

    setTimeout(() => {
      nextItem();
    }, 1200);
  };

  const handleEvaluation = (payload) => {
    const scoreLevel = Number(payload?.score_level ?? 0);

    if (isCorrect(scoreLevel)) {
      const pts = pointsForAttempt(attemptNum);
      const acc =
        typeof payload?.weighted_accuracy === "number"
          ? ` (${Math.round(payload.weighted_accuracy * 100)}%)`
          : "";

      setFeedback(
        attemptNum === 1
          ? `Correct sentence! (+3)${acc}`
          : attemptNum === 2
            ? `Correct! (+2)${acc}`
            : `Correct! (+1)${acc}`
      );
      finalizeItem({ awardedPoints: pts, payload, scoreLevel });
      return;
    }

    if (attemptNum === 1) {
      setFeedback("Not quite — try repeating the full sentence again.");
      setAttemptNum(2);
      setLockedUntilNext(false);
      return;
    }

    if (attemptNum === 2) {
      setFeedback("Still not quite — say it slowly and clearly.");
      setAttemptNum(3);
      setLockedUntilNext(false);
      return;
    }

    const shownPercent =
      payload?.exact_match
        ? 100
        : typeof payload?.weighted_accuracy === "number"
          ? Math.round(payload.weighted_accuracy * 100)
          : null;

    const acc = shownPercent != null ? ` (${shownPercent}%)` : "";
    setFeedback(`Incorrect. (+0)${acc}`);
    finalizeItem({ awardedPoints: 0, payload, scoreLevel });
  };

  const startRecording = async () => {
    if (isProcessing || completed || lockedUntilNext) return;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const recorder = new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        try {
          setIsProcessing(true);
          setLockedUntilNext(true);

          const blob = new Blob(chunksRef.current, { type: "audio/webm" });
          await sendAudio(blob);
        } finally {
          stream.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
          setIsProcessing(false);
        }
      };

      recorder.start();
      setIsRecording(true);
    } catch (e) {
      setFeedback("Microphone error — please allow access and try again.");
      setLockedUntilNext(false);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  };

  const sendAudio = async (blob) => {
    if (!currentItem) return;

    const derivedTargets = deriveSentenceTargets(currentItem.sentence);

    const reqContent =
      Array.isArray(currentItem.required_content) && currentItem.required_content.length > 0
        ? currentItem.required_content
        : derivedTargets.required_content;

    const reqFunction =
      Array.isArray(currentItem.required_function) && currentItem.required_function.length > 0
        ? currentItem.required_function
        : derivedTargets.required_function;

    const formData = new FormData();
    formData.append("audio", blob, "recording.webm");
    formData.append("target_sentence", currentItem.sentence);
    formData.append("required_content", reqContent.join(","));
    formData.append("required_function", reqFunction.join(","));

    try {
      const res = await fetch(`${API_BASE}/api/sentence-eval`, {
        method: "POST",
        body: formData,
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Failed to process audio");

      setAudioResult(data.text || "");
      handleEvaluation(data);
    } catch (e) {
      setFeedback("Failed to process audio — try again.");
      setLockedUntilNext(false);
    }
  };

  useEffect(() => {
    const postSessionScore = async () => {
      try {
        const token = await getToken();
        if (!token) return;

        const res = await fetch(`${API_BASE}/api/score`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            technique_id: TECHNIQUE_ID,
            delta_score: sessionPoints,
            session_size: sessionItems.length,
            items: itemResults,
          }),
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || "Failed to submit score");
        setScorePostResult(data);
      } catch (e) {
        setScorePostError(String(e.message || e));
      }
    };

    if (completed && !postedSessionRef.current) {
      postedSessionRef.current = true;
      postSessionScore();
    }
  }, [completed, sessionPoints, sessionItems.length, itemResults, getToken]);

  const recordDisabled = completed || isProcessing || lockedUntilNext;

  const progressDisplay = Math.min(
    currentIndex + (completed ? 0 : 1),
    sessionItems.length
  );

  return (
    <div className="basic-speech-content">
      <h1 className="basic-speech-title">Sentence Repetition</h1>

      <div className="practice-section">
        <h2>Repeat the sentence</h2>
        <p>Look at the image and say the full sentence shown.</p>

        <div style={{ marginTop: 10 }}>
          <strong>
            Progress: {progressDisplay}/{sessionItems.length}
          </strong>

          <span style={{ marginLeft: 12 }}>
            <strong>Session points:</strong> {sessionPoints}
          </span>

          {!completed && (
            <span style={{ marginLeft: 12 }}>
              <strong>Attempt:</strong> {attemptNum}/3
            </span>
          )}

          <span style={{ marginLeft: 12, opacity: 0.85 }}>
            <strong>Source:</strong> {contentSource === "pack" ? "Personalised" : "Default"}
          </span>

          <button
            onClick={toggleMute}
            style={{
              marginLeft: 14,
              padding: "6px 12px",
              borderRadius: "20px",
              border: "1px solid rgba(255,255,255,0.2)",
              background: isMuted ? "rgba(255, 80, 80, 0.15)" : "rgba(80, 200, 120, 0.15)",
              color: "inherit",
              fontWeight: 500,
              cursor: "pointer",
              transition: "all 0.2s ease",
            }}
            type="button"
          >
            {isMuted ? "🔇 Muted" : "🔊 Voice On"}
          </button>
        </div>

        {packError && (
          <div style={{ marginTop: 10, opacity: 0.8 }}>
            Personalised pack unavailable → using default set.
          </div>
        )}

        <div className="practice-cards">
          <div className="practice-card">
            {!completed ? (
              currentItem && (
                <div className="image-container">
                  <img
                    src={resolveImagePath(currentItem.image)}
                    alt="Sentence prompt"
                    className="practice-image"
                  />

                  <div style={{ marginTop: 14, textAlign: "center" }}>
                    <h3 style={{ marginBottom: 8 }}>Say:</h3>
                    <p style={{ fontSize: 22, fontWeight: 700 }}>
                      {currentItem.sentence}
                    </p>
                  </div>
                </div>
              )
            ) : null}

            {!completed && (
              <div className="recording-section">
                <button
                  onClick={isRecording ? stopRecording : startRecording}
                  className={isRecording ? "recording" : ""}
                  disabled={recordDisabled && !isRecording}
                >
                  {isRecording
                    ? "Stop Recording"
                    : isProcessing
                      ? "Processing..."
                      : "Start Recording"}
                </button>
              </div>
            )}

            {!completed && audioResult && (
              <div className="result-section">
                <h4>🗣️ You said:</h4>
                <p>{audioResult}</p>
              </div>
            )}

            {!completed && feedback && (
              <div
                className={`feedback-message ${feedback
                  .toLowerCase()
                  .replaceAll(" ", "-")
                  .replaceAll("!", "")
                  .replaceAll("—", "-")}`}
              >
                {feedback}
              </div>
            )}

            {completed && (
              <div style={{ marginTop: 14 }}>
                <h3>Session Complete</h3>

                <p>
                  Final points: <strong>{sessionPoints}</strong>
                </p>

                {scorePostResult?.analytics?.session_avg_sentence_accuracy != null && (
                  <p>
                    Average sentence accuracy:{" "}
                    <strong>
                      {Math.round(scorePostResult.analytics.session_avg_sentence_accuracy * 100)}%
                    </strong>
                  </p>
                )}

                {scorePostResult?.technique?.lifetime_avg_sentence_accuracy != null && (
                  <p>
                    Lifetime sentence accuracy:{" "}
                    <strong>
                      {Math.round(scorePostResult.technique.lifetime_avg_sentence_accuracy * 100)}%
                    </strong>
                  </p>
                )}

                {scorePostError && (
                  <p className="error-message">Score post error: {scorePostError}</p>
                )}

                {scorePostResult && <p style={{ opacity: 0.9 }}>Score saved.</p>}

                <div className="recording-section">
                  <button onClick={() => setSessionSeed((s) => s + 1)}>
                    Start New Session
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}