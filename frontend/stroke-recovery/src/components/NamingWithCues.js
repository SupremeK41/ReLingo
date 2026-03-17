// frontend/src/components/NamingWithCues.js
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@clerk/clerk-react";
import { useLocation } from "react-router-dom";

import "../App.css";
import "./BasicSpeech.css";
import wordPairs from "../data/wordPairs.json";
import {
  API_BASE,
  buildProgressDisplay,
  clearAdvanceTimeout,
  isBackendNoSpeech,
  pointsForAttempt,
  resolveImagePath,
  shuffleItems,
  stopBrowserSpeech,
} from "./therapySessionUtils";

const SESSION_SIZE = 10;
const TECHNIQUE_ID = "naming_with_cues";
const FALLBACK_ADVANCE_DELAY_MS = 1600;
const PERSONALISED_MODE = "personalised";
const CURATED_MODE = "curated";

function normaliseContentMode(rawMode) {
  return rawMode === CURATED_MODE ? CURATED_MODE : PERSONALISED_MODE;
}

function normaliseFallbackItem(item) {
  const word = String(item?.word || "").trim();

  return {
    word,
    image: String(item?.image || "").trim(),
    semantic_cue: String(item?.semantic_cue || `It is related to ${word}.`).trim(),
    phonemic_cue: String(
      item?.phonemic_cue || (word ? `It starts with '${word[0]}'.` : "")
    ).trim(),
    full_cue: String(item?.full_cue || word).trim(),
  };
}

function getDefaultItems() {
  return Array.isArray(wordPairs)
    ? wordPairs.map(normaliseFallbackItem).filter((item) => item.word)
    : [];
}

function buildSessionItems(items) {
  const shuffled = shuffleItems(items || []);
  return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
}

export default function NamingWithCues() {
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

  const [audioResult, setAudioResult] = useState("");
  const [error, setError] = useState(null);

  const [currentIndex, setCurrentIndex] = useState(0);
  const [feedback, setFeedback] = useState("");

  const [sessionPoints, setSessionPoints] = useState(0);
  const [attemptNum, setAttemptNum] = useState(1);
  const [cueLevel, setCueLevel] = useState(0); // 0 = none, 1 = semantic, 2 = phonemic, 3 = full answer
  const [completed, setCompleted] = useState(false);

  const [scorePostResult, setScorePostResult] = useState(null);
  const [scorePostError, setScorePostError] = useState(null);
  const [itemResults, setItemResults] = useState([]);

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);
  const postedSessionRef = useRef(false);
  const advanceTimeoutRef = useRef(null);
  const currentUtteranceRef = useRef(null);

  const [isMuted, setIsMuted] = useState(() => {
    try {
      return localStorage.getItem("relingo_muted_cues") === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem("relingo_muted_cues", isMuted ? "1" : "0");
    } catch {}
  }, [isMuted]);

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
    setIsMuted((current) => {
      const next = !current;

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
    setCompleted(false);
    setError(null);
    setIsRecording(false);
    setIsProcessing(false);
    setLockedUntilNext(false);

    setSessionPoints(0);
    setAttemptNum(1);
    setCueLevel(0);
    setItemResults([]);

    setScorePostResult(null);
    setScorePostError(null);
    postedSessionRef.current = false;
  };

  useEffect(() => {
    const loadPack = async () => {
      setPackError(null);
      setIsPackLoading(true);
      setContentSource("local");
      setBaseItems([]);
      setSessionItems([]);
      resetSessionState();

      try {
        const token = await getToken();

        if (!token) {
          const fallbackItems = getDefaultItems();
          setContentSource("local");
          setBaseItems(fallbackItems);
          setSessionItems(buildSessionItems(fallbackItems));
          return;
        }

        const response = await fetch(
          `${API_BASE}/api/content-pack/active/${TECHNIQUE_ID}?mode=${selectedMode}`,
          {
            headers: {
              Authorization: `Bearer ${token}`,
            },
          }
        );

        const data = await response.json();
        if (!response.ok) {
          throw new Error(data?.detail || "Failed to load content pack");
        }

        const resolvedMode = normaliseContentMode(
          response.headers.get("X-ReLingo-Resolved-Mode")
        );

        const items = Array.isArray(data?.items) ? data.items : [];
        const cleanedItems = items
          .map((item) => ({
            word: String(item?.word || "").trim(),
            image: String(item?.image || "").trim(),
            semantic_cue: String(item?.semantic_cue || "").trim(),
            phonemic_cue: String(item?.phonemic_cue || "").trim(),
            full_cue: String(item?.full_cue || item?.word || "").trim(),
          }))
          .filter((item) => item.word);

        if (cleanedItems.length > 0) {
          setContentSource(
            resolvedMode === CURATED_MODE ? "curated" : "personalised"
          );
          setBaseItems(cleanedItems);
          setSessionItems(buildSessionItems(cleanedItems));
        } else {
          const fallbackItems = getDefaultItems();
          setContentSource("local");
          setBaseItems(fallbackItems);
          setSessionItems(buildSessionItems(fallbackItems));
        }
      } catch (err) {
        const fallbackItems = getDefaultItems();
        setPackError(String(err.message || err));
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
    setFeedback("");
    setAudioResult("");
    setError(null);
    setLockedUntilNext(false);
    setAttemptNum(1);
    setCueLevel(0);
  };

  const nextItem = () => {
    const nextIndex = currentIndex + 1;

    if (nextIndex < sessionItems.length) {
      resetForNextItem(nextIndex);
    } else {
      clearAdvanceTimeout(advanceTimeoutRef);
      stopSpeaking();

      setCompleted(true);
      setFeedback("");
      setAudioResult("");
      setLockedUntilNext(false);
    }
  };

  const finalizeCurrentItem = ({ awardedPoints, payload, speechText }) => {
    const target = currentItem?.word || "";

    setSessionPoints((points) => points + awardedPoints);

    setItemResults((prev) => [
      ...prev,
      {
        target,
        awardedPoints,
        attemptsUsed: attemptNum,
        cueShown: cueLevel > 0,
        transcript: payload?.text || "",
        best_guess: payload?.best_guess || "",
        matched: !!payload?.matched,
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

  const handleEvaluationResult = (payload) => {
    if (isBackendNoSpeech(payload)) {
      const message = "Didn’t catch that. Try speaking a bit longer.";
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    const matched = !!payload?.matched;
    const answerWord = currentItem?.full_cue || currentItem?.word || "";

    if (matched) {
      const points = pointsForAttempt(attemptNum);

      const feedbackText = `Correct! (+${points})`;

      const speechText = answerWord
        ? `Correct. The word is ${answerWord}. You earned ${points} point${
            points === 1 ? "" : "s"
          }.`
        : `Correct. You earned ${points} point${points === 1 ? "" : "s"}.`;

      setFeedback(feedbackText);

      finalizeCurrentItem({
        awardedPoints: points,
        payload,
        speechText,
      });
      return;
    }

    if (attemptNum === 1) {
      const semanticCue = currentItem?.semantic_cue || "Here is your semantic cue.";
      const message = `Not quite. Here is your semantic cue: ${semanticCue}`;
      setCueLevel(1);
      setAttemptNum(2);
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    if (attemptNum === 2) {
      const phonemicCue = currentItem?.phonemic_cue || "Here is your phonemic cue.";
      const message = `Still not quite. Here is your phonemic cue: ${phonemicCue}`;
      setCueLevel(2);
      setAttemptNum(3);
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    setCueLevel(3);

    const feedbackText = `The word is ${answerWord}. Zero points for this one.`;
    const speechText = answerWord
      ? `The correct word is ${answerWord}. Zero points for this one.`
      : "Zero points for this one.";

    setFeedback(feedbackText);

    finalizeCurrentItem({
      awardedPoints: 0,
      payload,
      speechText,
    });
  };

  const handleUpload = async (audioBlob) => {
    if (!currentItem) return;

    const formData = new FormData();
    formData.append("audio", audioBlob, "recording.webm");

    const vocab = sessionItems.map((item) => item.word).join(",");
    formData.append("vocab", vocab);
    formData.append("target", currentItem.word);
    formData.append("attempt_num", String(attemptNum));
    formData.append("cue_shown", cueLevel > 0 ? "1" : "0");

    try {
      const response = await fetch(`${API_BASE}/api/speech-to-text`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error("Failed to process audio");
      }

      const data = await response.json();

      setAudioResult(data.text || "");
      setError(null);

      handleEvaluationResult(data);
    } catch (err) {
      setError("Failed to process audio: " + err.message);
      setLockedUntilNext(false);
    }
  };

  const startRecording = async () => {
    if (isPackLoading || isProcessing || completed || lockedUntilNext) return;

    try {
      setError(null);
      stopSpeaking();
      clearAdvanceTimeout(advanceTimeoutRef);

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      chunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        try {
          setIsProcessing(true);
          setLockedUntilNext(true);

          const audioBlob = new Blob(chunksRef.current, { type: "audio/webm" });
          await handleUpload(audioBlob);
        } finally {
          stream.getTracks().forEach((track) => track.stop());
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

  useEffect(() => {
    const postSessionScore = async () => {
      try {
        const token = await getToken();
        if (!token) return;

        const response = await fetch(`${API_BASE}/api/score`, {
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

        const data = await response.json();
        if (!response.ok) {
          throw new Error(data?.detail || "Failed to submit score");
        }

        setScorePostResult(data);
      } catch (err) {
        setScorePostError(String(err.message || err));
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
      <h1 className="basic-speech-title">Naming with Cues</h1>

      <div className="practice-section">
        <h2>Name the picture</h2>
        <p>Try naming the picture. If needed, the app will give helpful cues.</p>

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
            type="button"
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
          >
            {isMuted ? "Muted" : "Voice On"}
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
                  {selectedMode === CURATED_MODE ? "curated" : "personalised"}{" "}
                  content.
                </p>
              </div>
            )}

            {!isPackLoading && !completed && currentItem && (
              <div className="image-container">
                <img
                  src={resolveImagePath(currentItem.image)}
                  alt="Naming prompt"
                  className="practice-image"
                />

                {cueLevel >= 1 && (
                  <div
                    style={{
                      marginTop: 10,
                      padding: 10,
                      borderRadius: 10,
                      border: "1px solid rgba(255,255,255,0.15)",
                    }}
                  >
                    <strong>Semantic cue:</strong> {currentItem.semantic_cue}
                  </div>
                )}

                {cueLevel >= 2 && (
                  <div
                    style={{
                      marginTop: 10,
                      padding: 10,
                      borderRadius: 10,
                      border: "1px solid rgba(255,255,255,0.15)",
                    }}
                  >
                    <strong>Phonemic cue:</strong> {currentItem.phonemic_cue}
                  </div>
                )}

                {cueLevel >= 3 && (
                  <div
                    style={{
                      marginTop: 10,
                      padding: 10,
                      borderRadius: 10,
                      border: "1px solid rgba(255,255,255,0.15)",
                    }}
                  >
                    <strong>Answer:</strong> {currentItem.full_cue}
                  </div>
                )}
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
                <h4>You said:</h4>
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

            {error && <div className="error-message">{error}</div>}

            {!isPackLoading && completed && (
              <div style={{ marginTop: 14 }}>
                <h3>Session Complete</h3>
                <p>
                  Final points: <strong>{sessionPoints}</strong>
                </p>

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