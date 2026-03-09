// frontend/src/components/BasicSpeech.js
import React, { useEffect, useMemo, useRef, useState } from "react";
import "../App.css";
import "./BasicSpeech.css";

import { useAuth } from "@clerk/clerk-react";
import wordPairs from "../data/wordPairs.json";

const SESSION_SIZE = 10;
const TECHNIQUE_ID = "image_naming";
const API_BASE = "http://localhost:8000";

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function getCue(word) {
  if (!word) return "";
  return word[0].toUpperCase();
}

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

// attempt 1 -> 3 points, attempt 2 -> 2 points, attempt 3 (after cue) -> 1 point
function pointsForAttempt(attemptNum) {
  if (attemptNum === 1) return 3;
  if (attemptNum === 2) return 2;
  return 1;
}

// Backend "no-speech" guard: do NOT consume an attempt
function isNoSpeech(payload) {
  if (!payload) return false;
  const t = (payload.text || "").trim();
  if (t !== "") return false;

  if (payload.too_short) return true;
  if (typeof payload.no_speech_prob === "number" && payload.no_speech_prob >= 0.8) return true;
  if (payload.prompt_leak) return true;

  if (payload.best_guess == null && payload.distance == null && payload.rel_distance == null) return true;

  return false;
}

export default function BasicSpeech() {
  const { getToken } = useAuth();

  // -------------------------
  // content source
  // -------------------------
  const [contentSource, setContentSource] = useState("local"); // "pack" | "local"
  const [baseItems, setBaseItems] = useState(wordPairs); // [{word,image}]
  const [packError, setPackError] = useState(null);

  // -------------------------
  // existing state
  // -------------------------
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [lockedUntilNext, setLockedUntilNext] = useState(false);

  const [audioResult, setAudioResult] = useState("");
  const [error, setError] = useState(null);

  const [currentIndex, setCurrentIndex] = useState(0);
  const [feedback, setFeedback] = useState("");

  const [sessionPoints, setSessionPoints] = useState(0);

  const [attemptNum, setAttemptNum] = useState(1);
  const [showCue, setShowCue] = useState(false);

  const [completed, setCompleted] = useState(false);

  const [scorePostResult, setScorePostResult] = useState(null);
  const [scorePostError, setScorePostError] = useState(null);

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);

  const [sessionSeed, setSessionSeed] = useState(0);
  const postedSessionRef = useRef(false);

  const [itemResults, setItemResults] = useState([]);

  // -------------------------
  // Mute / TTS
  // -------------------------
  const [isMuted, setIsMuted] = useState(() => {
    try {
      return localStorage.getItem("relingo_muted") === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem("relingo_muted", isMuted ? "1" : "0");
    } catch {}
  }, [isMuted]);

  const toggleMute = () => {
    setIsMuted((m) => {
      const next = !m;
      if (next && "speechSynthesis" in window) window.speechSynthesis.cancel();
      return next;
    });
  };

  function speak(text) {
    if (isMuted) return;
    if (!text) return;
    if (!("speechSynthesis" in window)) return;

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.9;
    utterance.pitch = 1;
    utterance.volume = 1;
    window.speechSynthesis.speak(utterance);
  }

  useEffect(() => {
    if (!feedback) return;
    speak(feedback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedback]);

  // -------------------------
  // load active pack (image_naming) once user is signed in
  // -------------------------
  useEffect(() => {
    const loadPack = async () => {
      setPackError(null);

      try {
        const token = await getToken();
        if (!token) {
          setContentSource("local");
          setBaseItems(wordPairs);
          return;
        }

        const res = await fetch(`${API_BASE}/api/content-pack/active/${TECHNIQUE_ID}`, {
          headers: { Authorization: `Bearer ${token}` },
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || "Failed to load content pack");

        const items = Array.isArray(data?.items) ? data.items : [];

        const cleaned = items
          .map((it) => ({
            word: String(it?.word || "").trim(),
            image: String(it?.image || "").trim(),
          }))
          .filter((it) => it.word);

        if (cleaned.length > 0) {
          setContentSource("pack");
          setBaseItems(cleaned);
        } else {
          setContentSource("local");
          setBaseItems(wordPairs);
        }
      } catch (e) {
        setPackError(String(e.message || e));
        setContentSource("local");
        setBaseItems(wordPairs);
      }
    };

    loadPack();
  }, [getToken]);

  // -------------------------
  // Session words derived from baseItems (pack OR local)
  // -------------------------
  const sessionWords = useMemo(() => {
    const shuffled = shuffle(baseItems || []);
    return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
  }, [sessionSeed, baseItems]);

  const currentItem = sessionWords[currentIndex];

  // Reset state when session changes
  useEffect(() => {
    setCurrentIndex(0);
    setFeedback("");
    setAudioResult("");
    setCompleted(false);
    setError(null);
    setIsRecording(false);
    setIsProcessing(false);
    setLockedUntilNext(false);
    setScorePostResult(null);
    setScorePostError(null);
    postedSessionRef.current = false;

    setSessionPoints(0);
    setAttemptNum(1);
    setShowCue(false);
    setItemResults([]);
  }, [sessionWords]);

  const resetForNextItem = (nextIndex) => {
    setCurrentIndex(nextIndex);
    setFeedback("");
    setAudioResult("");
    setError(null);
    setLockedUntilNext(false);
    setAttemptNum(1);
    setShowCue(false);
  };

  const nextItem = () => {
    const nextIndex = currentIndex + 1;
    if (nextIndex < sessionWords.length) {
      resetForNextItem(nextIndex);
    } else {
      setCompleted(true);
      setFeedback("");
      setAudioResult("");
      setLockedUntilNext(false);
    }
  };

  const finalizeCurrentItem = ({ awardedPoints, payload }) => {
    const target = currentItem?.word || "";

    setSessionPoints((p) => p + awardedPoints);

    setItemResults((prev) => [
      ...prev,
      {
        target,
        awardedPoints,
        attemptsUsed: attemptNum,
        cueShown: showCue,
        transcript: payload?.text || "",
        best_guess: payload?.best_guess || "",
        matched: !!payload?.matched,
      },
    ]);

    setTimeout(() => {
      nextItem();
    }, 1200);
  };

  const handleEvaluationResult = (payload) => {
    if (isNoSpeech(payload)) {
      setFeedback("Didn’t catch that—try speaking a bit longer.");
      setLockedUntilNext(false);
      return;
    }

    const matched = !!payload?.matched;

    if (matched) {
      const pts = pointsForAttempt(attemptNum);
      setFeedback(
        attemptNum === 1
          ? "Correct! (+3)"
          : attemptNum === 2
          ? "Correct! (+2)"
          : "Correct after cue! (+1)"
      );
      finalizeCurrentItem({ awardedPoints: pts, payload });
      return;
    }

    if (attemptNum === 1) {
      setFeedback("Incorrect — here’s a cue. Try again.");
      setShowCue(true);
      setAttemptNum(2);
      setLockedUntilNext(false);
      return;
    }

    if (attemptNum === 2) {
      setFeedback("Still not quite — use the cue and try once more.");
      setShowCue(true);
      setAttemptNum(3);
      setLockedUntilNext(false);
      return;
    }

    setFeedback("Incorrect. (+0)");
    finalizeCurrentItem({ awardedPoints: 0, payload });
  };

  const startRecording = async () => {
    if (isProcessing || completed || lockedUntilNext) return;

    try {
      setError(null);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      chunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };

      mediaRecorder.onstop = async () => {
        try {
          setIsProcessing(true);
          setLockedUntilNext(true);

          const audioBlob = new Blob(chunksRef.current, { type: "audio/webm" });
          await handleUpload(audioBlob);
        } finally {
          stream.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
          setIsProcessing(false);
        }
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (err) {
      setError("Error accessing microphone: " + err.message);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  };

  const handleUpload = async (audioBlob) => {
    if (!currentItem) return;

    const formData = new FormData();
    formData.append("audio", audioBlob, "recording.webm");

    const vocab = sessionWords.map((x) => x.word).join(",");
    formData.append("vocab", vocab);
    formData.append("target", currentItem.word);
    formData.append("attempt_num", String(attemptNum));
    formData.append("cue_shown", showCue ? "1" : "0");

    try {
      const response = await fetch(`${API_BASE}/api/speech-to-text`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) throw new Error("Failed to process audio");

      const data = await response.json();

      setAudioResult(data.text || "");
      setError(null);

      handleEvaluationResult(data);
    } catch (e) {
      setError("Failed to process audio: " + e.message);
      setLockedUntilNext(false);
    }
  };

  // Post total session points once session is completed
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
            session_size: sessionWords.length,
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
  }, [completed, sessionPoints, sessionWords.length, itemResults, getToken]);

  const resetSession = () => {
    setSessionSeed((s) => s + 1);
  };

  const recordDisabled = completed || isProcessing || lockedUntilNext;
  const progressDisplay = Math.min(
    currentIndex + (completed ? 0 : 1),
    sessionWords.length
  );
  const cueLetter = !completed && currentItem ? getCue(currentItem.word) : "";

  return (
    <div className="basic-speech-content">
      <h1 className="basic-speech-title">Basic Speech Practice</h1>

      <div className="practice-section">
        <h2>Say what you see!</h2>
        <p>Look at the image and say the word it represents.</p>

        <div style={{ marginTop: 10 }}>
          <strong>
            Progress: {progressDisplay}/{sessionWords.length}
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
                    alt={currentItem.word}
                    className="practice-image"
                  />

                  <p className="target-word">🎯 Target word: {currentItem.word}</p>

                  {showCue && (
                    <div
                      style={{
                        marginTop: 10,
                        padding: 10,
                        borderRadius: 10,
                        border: "1px solid rgba(255,255,255,0.15)",
                      }}
                    >
                      <strong>Cue:</strong> starts with <strong>{cueLetter}</strong>
                    </div>
                  )}
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

            {error && <div className="error-message">{error}</div>}

            {completed && (
              <div style={{ marginTop: 14 }}>
                <h3>Session Complete</h3>
                <p>
                  Final points: <strong>{sessionPoints}</strong>
                </p>

                {scorePostError && (
                  <p className="error-message">Score post error: {scorePostError}</p>
                )}
                {scorePostResult && <p style={{ opacity: 0.9 }}>Score saved.</p>}

                <div className="recording-section">
                  <button onClick={resetSession}>Start New Session</button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}