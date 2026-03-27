import React, { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@clerk/clerk-react";
import { useLocation } from "react-router-dom";

import "../App.css";
import "./BasicSpeech.css";
import {
  API_BASE,
  buildProgressDisplay,
  isBackendNoSpeech,
  pointsForAttempt,
  resolveImagePath,
  shuffleItems,
  stopBrowserSpeech,
} from "./therapySessionUtils";

const SESSION_SIZE = 10;
const TECHNIQUE_ID = "semantic_feature_analysis";
const PERSONALISED_MODE = "personalised";
const CURATED_MODE = "curated";

const DEFAULT_PROMPTS = [
  "What type of thing is it?",
  "What is it used for or what does it do?",
  "Where might you find it?",
  "What does it look like?",
  "What is it related to?",
  "What actions are linked to it?",
];

function normaliseContentMode(rawMode) {
  return rawMode === CURATED_MODE ? CURATED_MODE : PERSONALISED_MODE;
}

function normaliseSfaItem(item) {
  const prompts = Array.isArray(item?.feature_prompts)
    ? item.feature_prompts.filter(Boolean)
    : [];

  return {
    word: String(item?.word || "").trim(),
    image: String(item?.image || "").trim(),
    tags: Array.isArray(item?.tags) ? item.tags : [],
    semantic_hint: String(item?.semantic_hint || "").trim(),
    feature_prompts: prompts.length > 0 ? prompts : DEFAULT_PROMPTS,
  };
}

function buildMatchedFeedback(attemptNum) {
  if (attemptNum === 1) {
    return "Good description. Plus 3 points.";
  }
  if (attemptNum === 2) {
    return "Correct after support. Plus 2 points.";
  }
  return "Correct on the final attempt. Plus 1 point.";
}

function buildFinalIncorrectFeedback() {
  return "Moving on to the next one. Zero points for this item.";
}

function buildResolvedSpeech({ matched, bestGuess, target, awardedPoints }) {
  const guess = String(bestGuess || "").trim();
  const word = String(target || "").trim();

  if (matched) {
    if (guess && word) {
      return `Good work. The AI guessed: ${guess}. The correct word is: ${word}. You earned ${awardedPoints} point${
        awardedPoints === 1 ? "" : "s"
      }.`;
    }
    if (word) {
      return `Good work. The correct word is: ${word}. You earned ${awardedPoints} point${
        awardedPoints === 1 ? "" : "s"
      }.`;
    }
    return `Good work. You earned ${awardedPoints} point${
      awardedPoints === 1 ? "" : "s"
    }.`;
  }

  if (guess && word) {
    return `The AI guessed: ${guess}. The correct word is: ${word}.`;
  }
  if (word) {
    return `The correct word is: ${word}.`;
  }
  if (guess) {
    return `The AI guessed: ${guess}.`;
  }

  return "Moving on to the next one.";
}

function buildSessionItems(items) {
  const shuffled = shuffleItems(items || []);
  return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
}

export default function SemanticFeatureAnalysis() {
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
  const [showHint, setShowHint] = useState(false);
  const [completed, setCompleted] = useState(false);

  const [scorePostResult, setScorePostResult] = useState(null);
  const [scorePostError, setScorePostError] = useState(null);

  const [itemResults, setItemResults] = useState([]);
  const [lastEvaluation, setLastEvaluation] = useState(null);

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);
  const postedSessionRef = useRef(false);
  const currentUtteranceRef = useRef(null);

  const [isMuted, setIsMuted] = useState(() => {
    try {
      return localStorage.getItem("relingo_muted_sfa") === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem("relingo_muted_sfa", isMuted ? "1" : "0");
    } catch {}
  }, [isMuted]);

  function stopSpeaking() {
    stopBrowserSpeech();
    currentUtteranceRef.current = null;
  }

  function speak(text, options = {}) {
    const { onEnd } = options;

    if (!text) {
      if (typeof onEnd === "function") onEnd();
      return;
    }

    if (isMuted || !("speechSynthesis" in window)) {
      if (typeof onEnd === "function") onEnd();
      return;
    }

    stopSpeaking();

    const utterance = new SpeechSynthesisUtterance(text);
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
  }

  useEffect(() => {
    return () => {
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
    stopSpeaking();

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
    setLastEvaluation(null);
    postedSessionRef.current = false;

    setSessionPoints(0);
    setAttemptNum(1);
    setShowHint(false);
    setItemResults([]);
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
          setContentSource("local");
          setBaseItems([]);
          setSessionItems([]);
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
          .map(normaliseSfaItem)
          .filter((item) => item.word && item.image);

        if (cleanedItems.length > 0) {
          setContentSource(
            resolvedMode === CURATED_MODE ? "curated" : "personalised"
          );
          setBaseItems(cleanedItems);
          setSessionItems(buildSessionItems(cleanedItems));
        } else {
          setContentSource("local");
          setBaseItems([]);
          setSessionItems([]);
        }
      } catch (err) {
        setPackError(String(err.message || err));
        setContentSource("local");
        setBaseItems([]);
        setSessionItems([]);
      } finally {
        setIsPackLoading(false);
      }
    };

    loadPack();
  }, [getToken, selectedMode]);

  const currentItem = !isPackLoading ? sessionItems[currentIndex] : null;

  const resetForNextItem = (nextIndex) => {
    stopSpeaking();

    setCurrentIndex(nextIndex);
    setFeedback("");
    setAudioResult("");
    setError(null);
    setLockedUntilNext(false);
    setAttemptNum(1);
    setShowHint(false);
    setLastEvaluation(null);
  };

  const nextItem = () => {
    const nextIndex = currentIndex + 1;

    if (nextIndex < sessionItems.length) {
      resetForNextItem(nextIndex);
    } else {
      stopSpeaking();
      setCompleted(true);
      setFeedback("");
      setAudioResult("");
      setLockedUntilNext(false);
      setLastEvaluation(null);
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
        cueShown: showHint,
        transcript: payload?.text || "",
        best_guess: payload?.best_guess || "",
        matched: !!payload?.matched,
      },
    ]);

    speak(speechText, {
      onEnd: () => {
        nextItem();
      },
    });
  };

  const handleEvaluationResult = (payload) => {
    if (isBackendNoSpeech(payload)) {
      setLastEvaluation({
        transcript: "",
        best_guess: "",
        matched: false,
        target: currentItem?.word || "",
        revealAnswer: false,
      });

      const message = "Didn’t catch that. Try describing it a bit longer.";
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    const matched = !!payload?.matched;
    const targetWord = currentItem?.word || "";

    const evaluationForState = {
      transcript: payload?.text || "",
      best_guess: payload?.best_guess || "",
      matched,
      target: targetWord,
      revealAnswer: matched || attemptNum >= 3,
    };

    setLastEvaluation(evaluationForState);

    if (matched) {
      const points = pointsForAttempt(attemptNum);
      const feedbackText = buildMatchedFeedback(attemptNum);
      const speechText = buildResolvedSpeech({
        matched: true,
        bestGuess: payload?.best_guess || "",
        target: targetWord,
        awardedPoints: points,
      });

      setFeedback(feedbackText);

      finalizeCurrentItem({
        awardedPoints: points,
        payload,
        speechText,
      });
      return;
    }

    if (attemptNum === 1) {
      setShowHint(true);
      setAttemptNum(2);

      const message = "Good try. Here is a semantic hint to help you.";
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    if (attemptNum === 2) {
      setAttemptNum(3);

      const message = "Try once more using the picture and the hint.";
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    const feedbackText = buildFinalIncorrectFeedback();
    const speechText = buildResolvedSpeech({
      matched: false,
      bestGuess: payload?.best_guess || "",
      target: targetWord,
      awardedPoints: 0,
    });

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
    formData.append("target_word", currentItem.word);
    formData.append("vocab", sessionItems.map((item) => item.word).join(","));
    formData.append("semantic_hint", currentItem.semantic_hint || "");
    formData.append("tags", (currentItem.tags || []).join(","));
    formData.append("attempt_num", String(attemptNum));
    formData.append("cue_shown", showHint ? "1" : "0");

    try {
      const response = await fetch(`${API_BASE}/api/sfa-eval`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error("Failed to evaluate semantic feature response");
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

  const showResultCard = !completed && lastEvaluation;
  const aiGuess = String(lastEvaluation?.best_guess || "").trim();
  const revealedWord =
    lastEvaluation?.revealAnswer && lastEvaluation?.target
      ? String(lastEvaluation.target).trim()
      : "";

  const sourceLabel = isPackLoading
    ? "Loading..."
    : contentSource === "curated"
    ? "Curated Library"
    : contentSource === "personalised"
    ? "Personalised Pack"
    : "Default";

  return (
    <div className="basic-speech-content">
      <h1 className="basic-speech-title">Semantic Feature Analysis</h1>

      <div className="practice-section">
        <h2>Describe the picture</h2>
        <p>
          Look at the image and describe it without saying the answer directly.
          Think about what it is, what it does, where you find it, and what it
          looks like.
        </p>

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
              ? "Curated pack unavailable."
              : "Personalised pack unavailable."}
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
                  alt="Semantic feature analysis prompt"
                  className="practice-image"
                />

                <div
                  style={{
                    marginTop: 14,
                    padding: 14,
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.12)",
                    textAlign: "left",
                    background: "rgba(255,255,255,0.04)",
                    maxWidth: 540,
                    marginLeft: "auto",
                    marginRight: "auto",
                  }}
                >
                  <strong>Try describing:</strong>
                  <ul style={{ marginTop: 8, paddingLeft: 20 }}>
                    {(currentItem.feature_prompts || DEFAULT_PROMPTS).map(
                      (prompt, index) => (
                        <li key={`${prompt}-${index}`} style={{ marginBottom: 4 }}>
                          {prompt}
                        </li>
                      )
                    )}
                  </ul>
                </div>

                {showHint && currentItem.semantic_hint && (
                  <div
                    style={{
                      marginTop: 12,
                      padding: 12,
                      borderRadius: 12,
                      border: "1px solid rgba(255,255,255,0.15)",
                      background: "rgba(255,255,255,0.05)",
                      maxWidth: 540,
                      marginLeft: "auto",
                      marginRight: "auto",
                      textAlign: "left",
                    }}
                  >
                    <strong>Semantic hint:</strong> {currentItem.semantic_hint}
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
              <div
                className="result-section"
                style={{
                  marginTop: 18,
                  padding: 16,
                  borderRadius: 14,
                  border: "1px solid rgba(255,255,255,0.14)",
                  background: "rgba(255,255,255,0.06)",
                  maxWidth: 900,
                  marginLeft: "auto",
                  marginRight: "auto",
                }}
              >
                <h4 style={{ marginBottom: 8 }}>You said:</h4>
                <p
                  style={{
                    margin: 0,
                    fontSize: "1.1rem",
                    lineHeight: 1.5,
                    wordBreak: "break-word",
                  }}
                >
                  {audioResult}
                </p>
              </div>
            )}

            {showResultCard && (
              <div
                style={{
                  marginTop: 18,
                  padding: 18,
                  borderRadius: 16,
                  border: "1px solid rgba(255,255,255,0.18)",
                  background: "rgba(20, 20, 20, 0.55)",
                  color: "#ffffff",
                  textAlign: "left",
                  width: "100%",
                  maxWidth: 900,
                  marginLeft: "auto",
                  marginRight: "auto",
                  boxShadow: "0 10px 30px rgba(0,0,0,0.18)",
                }}
              >
                <div
                  style={{
                    display: "grid",
                    gap: 12,
                  }}
                >
                  <div
                    style={{
                      padding: 12,
                      borderRadius: 12,
                      background: "rgba(255,255,255,0.06)",
                      border: "1px solid rgba(255,255,255,0.08)",
                    }}
                  >
                    <div style={{ fontSize: 14, opacity: 0.8, marginBottom: 6 }}>
                      AI guess
                    </div>
                    <div style={{ fontSize: "1.25rem", fontWeight: 700 }}>
                      {aiGuess || "No clear guess"}
                    </div>
                  </div>

                  {revealedWord && (
                    <div
                      style={{
                        padding: 12,
                        borderRadius: 12,
                        background: "rgba(80, 200, 120, 0.12)",
                        border: "1px solid rgba(80, 200, 120, 0.28)",
                      }}
                    >
                      <div style={{ fontSize: 14, opacity: 0.85, marginBottom: 6 }}>
                        Correct word
                      </div>
                      <div style={{ fontSize: "1.35rem", fontWeight: 800 }}>
                        {revealedWord}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {!isPackLoading && !completed && feedback && (
              <div
                className={`feedback-message ${feedback
                  .toLowerCase()
                  .replaceAll(" ", "-")
                  .replaceAll("!", "")
                  .replaceAll("—", "-")
                  .replaceAll(":", "")}`}
                style={{
                  marginTop: 16,
                  maxWidth: 900,
                  marginLeft: "auto",
                  marginRight: "auto",
                }}
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