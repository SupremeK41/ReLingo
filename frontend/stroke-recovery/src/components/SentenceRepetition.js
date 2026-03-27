import React, { useEffect, useMemo, useRef, useState } from "react";
import "../App.css";
import "./BasicSpeech.css";

import { useAuth } from "@clerk/clerk-react";
import { useLocation } from "react-router-dom";
import sentenceData from "../data/sentenceProduction.json";
import {
  API_BASE,
  buildProgressDisplay,
  clearAdvanceTimeout,
  pointsForAttempt,
  resolveImagePath,
  shuffleItems,
  stopBrowserSpeech,
} from "./therapySessionUtils";

const SESSION_SIZE = 5;
const TECHNIQUE_ID = "sentence_repetition";
const FALLBACK_ADVANCE_DELAY_MS = 1600;
const PERSONALISED_MODE = "personalised";
const CURATED_MODE = "curated";

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

function isCorrect(scoreLevel) {
  return Number(scoreLevel) >= 2;
}

function normaliseContentMode(rawMode) {
  return rawMode === CURATED_MODE ? CURATED_MODE : PERSONALISED_MODE;
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
        ? it.required_content.map((x) => String(x || "").trim()).filter(Boolean)
        : derivedTargets.required_content,
    required_function:
      Array.isArray(it?.required_function) && it.required_function.length > 0
        ? it.required_function.map((x) => String(x || "").trim()).filter(Boolean)
        : derivedTargets.required_function,
  };
}

function getDefaultSentenceItems() {
  return Array.isArray(sentenceData)
    ? sentenceData.map(normaliseSentenceItem).filter((it) => it.sentence)
    : [];
}

function buildSessionItems(items) {
  const shuffled = shuffleItems(items || []);
  return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
}

export default function SentenceRepetition() {
  const { getToken } = useAuth();
  const location = useLocation();

  const selectedMode = useMemo(() => {
    const stateMode = location.state?.contentMode;
    const queryMode = new URLSearchParams(location.search).get("mode");
    return normaliseContentMode(stateMode || queryMode);
  }, [location.state, location.search]);

  const [contentSource, setContentSource] = useState("local");
  const [baseItems, setBaseItems] = useState([]);
  const [sessionItems, setSessionItems] = useState([]);
  const [packError, setPackError] = useState(null);
  const [isPackLoading, setIsPackLoading] = useState(true);

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
  const advanceTimeoutRef = useRef(null);
  const currentUtteranceRef = useRef(null);

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

  const stopSpeaking = () => {
    stopBrowserSpeech();
    currentUtteranceRef.current = null;
  };

  const speak = (text, { onEnd } = {}) => {
    const finalText = String(text || "").trim();

    if (!finalText || isMuted || !("speechSynthesis" in window)) {
      if (typeof onEnd === "function") {
        onEnd();
      }
      return;
    }

    stopSpeaking();

    const utterance = new SpeechSynthesisUtterance(finalText);
    utterance.rate = 0.9;
    utterance.pitch = 1;
    utterance.volume = 1;

    utterance.onend = () => {
      if (currentUtteranceRef.current === utterance) {
        currentUtteranceRef.current = null;
      }
      if (typeof onEnd === "function") {
        onEnd();
      }
    };

    utterance.onerror = () => {
      if (currentUtteranceRef.current === utterance) {
        currentUtteranceRef.current = null;
      }
      if (typeof onEnd === "function") {
        onEnd();
      }
    };

    currentUtteranceRef.current = utterance;
    window.speechSynthesis.speak(utterance);
  };

  const scheduleNextItemFallback = () => {
    clearAdvanceTimeout(advanceTimeoutRef);
    advanceTimeoutRef.current = setTimeout(() => {
      nextItem();
    }, FALLBACK_ADVANCE_DELAY_MS);
  };

  useEffect(() => {
    try {
      localStorage.setItem("rel_mute_sentence", isMuted ? "1" : "0");
    } catch {}
  }, [isMuted]);

  useEffect(() => {
    return () => {
      clearAdvanceTimeout(advanceTimeoutRef);
      stopSpeaking();

      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
    };
  }, []);

  const toggleMute = () => {
    setIsMuted((m) => {
      const next = !m;
      if (next) {
        stopSpeaking();
      }
      return next;
    });
  };

  const resetSessionState = () => {
    clearAdvanceTimeout(advanceTimeoutRef);
    stopSpeaking();

    setCurrentIndex(0);
    setFeedback("");
    setAudioResult("");
    setAttemptNum(1);
    setSessionPoints(0);
    setCompleted(false);
    setLockedUntilNext(false);
    setIsRecording(false);
    setIsProcessing(false);

    postedSessionRef.current = false;
    setScorePostResult(null);
    setScorePostError(null);
    setItemResults([]);
  };

  useEffect(() => {
    const loadPack = async () => {
      setIsPackLoading(true);
      setPackError(null);
      setContentSource("local");
      setBaseItems([]);
      setSessionItems([]);
      resetSessionState();

      try {
        const token = await getToken();
        if (!token) {
          const fallbackItems = getDefaultSentenceItems();
          setContentSource("local");
          setBaseItems(fallbackItems);
          setSessionItems(buildSessionItems(fallbackItems));
          return;
        }

        const res = await fetch(
          `${API_BASE}/api/content-pack/active/${TECHNIQUE_ID}?mode=${selectedMode}`,
          {
            headers: { Authorization: `Bearer ${token}` },
          }
        );

        const data = await res.json();
        if (!res.ok) {
          throw new Error(data?.detail || "Failed to load content pack");
        }

        const resolvedMode = normaliseContentMode(
          res.headers.get("X-ReLingo-Resolved-Mode")
        );

        const items = Array.isArray(data?.items) ? data.items : [];
        const cleaned = items
          .map(normaliseSentenceItem)
          .filter((it) => it.sentence);

        if (cleaned.length > 0) {
          setContentSource(
            resolvedMode === CURATED_MODE ? "curated" : "personalised"
          );
          setBaseItems(cleaned);
          setSessionItems(buildSessionItems(cleaned));
        } else {
          const fallbackItems = getDefaultSentenceItems();
          setContentSource("local");
          setBaseItems(fallbackItems);
          setSessionItems(buildSessionItems(fallbackItems));
        }
      } catch (e) {
        const fallbackItems = getDefaultSentenceItems();
        setPackError(String(e.message || e));
        setContentSource("local");
        setBaseItems(fallbackItems);
        setSessionItems(buildSessionItems(fallbackItems));
      } finally {
        setIsPackLoading(false);
      }
    };

    loadPack();
  }, [getToken, selectedMode]);

  const currentItem = !isPackLoading ? sessionItems[currentIndex] : null;

  const resetForNextItem = (nextIndex) => {
    clearAdvanceTimeout(advanceTimeoutRef);
    stopSpeaking();

    setCurrentIndex(nextIndex);
    setAttemptNum(1);
    setFeedback("");
    setAudioResult("");
    setLockedUntilNext(false);
  };

  const nextItem = () => {
    const next = currentIndex + 1;

    if (next < sessionItems.length) {
      resetForNextItem(next);
    } else {
      clearAdvanceTimeout(advanceTimeoutRef);
      stopSpeaking();

      setCompleted(true);
      setFeedback("");
      setAudioResult("");
      setLockedUntilNext(false);
    }
  };

  const finalizeItem = ({ awardedPoints, payload, scoreLevel, speechText }) => {
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

    if (isMuted || !("speechSynthesis" in window)) {
      scheduleNextItemFallback();
      return;
    }

    speak(speechText, {
      onEnd: () => {
        nextItem();
      },
    });
  };

  const handleEvaluation = (payload) => {
    const scoreLevel = Number(payload?.score_level ?? 0);

    if (isCorrect(scoreLevel)) {
      const pts = pointsForAttempt(attemptNum);
      const shownPercent =
        typeof payload?.weighted_accuracy === "number"
          ? Math.round(payload.weighted_accuracy * 100)
          : null;

      const accText = shownPercent != null ? ` (${shownPercent}%)` : "";
      const feedbackText =
        attemptNum === 1
          ? `Correct sentence! (+3)${accText}`
          : attemptNum === 2
          ? `Correct! (+2)${accText}`
          : `Correct! (+1)${accText}`;

      const speechText =
        shownPercent != null
          ? `Correct sentence. You earned ${pts} point${
              pts === 1 ? "" : "s"
            }. Accuracy ${shownPercent} percent.`
          : `Correct sentence. You earned ${pts} point${pts === 1 ? "" : "s"}.`;

      setFeedback(feedbackText);
      finalizeItem({ awardedPoints: pts, payload, scoreLevel, speechText });
      return;
    }

    if (attemptNum === 1) {
      const message = "Not quite — try repeating the full sentence again.";
      setFeedback(message);
      setAttemptNum(2);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    if (attemptNum === 2) {
      const message = "Still not quite — say it slowly and clearly.";
      setFeedback(message);
      setAttemptNum(3);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    const shownPercent =
      payload?.exact_match
        ? 100
        : typeof payload?.weighted_accuracy === "number"
        ? Math.round(payload.weighted_accuracy * 100)
        : null;

    const accText = shownPercent != null ? ` (${shownPercent}%)` : "";
    const feedbackText = `Incorrect. (+0)${accText}`;
    const speechText =
      shownPercent != null
        ? `Incorrect. Zero points. Accuracy ${shownPercent} percent.`
        : "Incorrect. Zero points.";

    setFeedback(feedbackText);
    finalizeItem({ awardedPoints: 0, payload, scoreLevel, speechText });
  };

  const startRecording = async () => {
    if (isPackLoading || isProcessing || completed || lockedUntilNext) return;

    try {
      stopSpeaking();
      clearAdvanceTimeout(advanceTimeoutRef);

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
      const message = "Microphone error — please allow access and try again.";
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
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
      Array.isArray(currentItem.required_content) &&
      currentItem.required_content.length > 0
        ? currentItem.required_content
        : derivedTargets.required_content;

    const reqFunction =
      Array.isArray(currentItem.required_function) &&
      currentItem.required_function.length > 0
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
      const message = "Failed to process audio — try again.";
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
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

  const resetSession = () => {
    resetSessionState();
    setSessionItems(buildSessionItems(baseItems));
  };

  const recordDisabled =
    isPackLoading || completed || isProcessing || lockedUntilNext;

  const progressDisplay = isPackLoading
    ? 0
    : buildProgressDisplay(currentIndex, completed, sessionItems.length);

  const sourceLabel = isPackLoading
    ? "Loading..."
    : contentSource === "curated"
    ? "Curated Library"
    : contentSource === "personalised"
    ? "Personalised Pack"
    : "Default";

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

          {!completed && !isPackLoading && (
            <span style={{ marginLeft: 12 }}>
              <strong>Attempt:</strong> {attemptNum}/3
            </span>
          )}

          <span style={{ marginLeft: 12, opacity: 0.85 }}>
            <strong>Source:</strong> {sourceLabel}
          </span>

          <button
            onClick={toggleMute}
            style={{
              marginLeft: 14,
              padding: "6px 12px",
              borderRadius: "20px",
              border: "1px solid rgba(255,255,255,0.2)",
              background: isMuted
                ? "rgba(255, 80, 80, 0.15)"
                : "rgba(80, 200, 120, 0.15)",
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
            {selectedMode === CURATED_MODE
              ? "Curated pack unavailable → using default set."
              : "Personalised pack unavailable → using default set."}
          </div>
        )}

        <div className="practice-cards">
          <div className="practice-card">
            {isPackLoading && (
              <div style={{ padding: "2rem 1rem", textAlign: "center" }}>
                <h3 style={{ marginBottom: "1rem" }}>Loading session...</h3>
                <p style={{ opacity: 0.8 }}>
                  Preparing{" "}
                  {selectedMode === CURATED_MODE
                    ? "curated"
                    : "personalised"}{" "}
                  content.
                </p>
              </div>
            )}

            {!isPackLoading && !completed && currentItem && (
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
            )}

            {!isPackLoading && !completed && (
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

            {!isPackLoading && !completed && audioResult && (
              <div className="result-section">
                <h4>🗣️ You said:</h4>
                <p>{audioResult}</p>
              </div>
            )}

            {!isPackLoading && !completed && feedback && (
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

            {!isPackLoading && completed && (
              <div style={{ marginTop: 14 }}>
                <h3>Session Complete</h3>

                <p>
                  Final points: <strong>{sessionPoints}</strong>
                </p>

                {scorePostResult?.analytics?.session_avg_sentence_accuracy !=
                  null && (
                  <p>
                    Average sentence accuracy:{" "}
                    <strong>
                      {Math.round(
                        scorePostResult.analytics.session_avg_sentence_accuracy *
                          100
                      )}
                      %
                    </strong>
                  </p>
                )}

                {scorePostResult?.technique?.lifetime_avg_sentence_accuracy !=
                  null && (
                  <p>
                    Lifetime sentence accuracy:{" "}
                    <strong>
                      {Math.round(
                        scorePostResult.technique
                          .lifetime_avg_sentence_accuracy * 100
                      )}
                      %
                    </strong>
                  </p>
                )}

                {scorePostError && (
                  <p className="error-message">
                    Score post error: {scorePostError}
                  </p>
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