// frontend/src/components/ActionVerbNaming.js
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@clerk/clerk-react";
import { useLocation } from "react-router-dom";

import "../App.css";
import "./BasicSpeech.css";
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
const TECHNIQUE_ID = "action_verb_naming";
const FALLBACK_ADVANCE_DELAY_MS = 1600;
const PERSONALISED_MODE = "personalised";
const CURATED_MODE = "curated";

function normaliseContentMode(rawMode) {
  return rawMode === CURATED_MODE ? CURATED_MODE : PERSONALISED_MODE;
}

function getCueFromVerb(verb) {
  if (!verb) return "";
  return String(verb).trim()[0]?.toUpperCase() || "";
}

function normaliseActionVerbItem(item) {
  const verb = String(item?.verb || "").trim();
  const baseVerb = String(item?.base_verb || "").trim();
  const semanticHint = String(item?.semantic_hint || "").trim();
  const subject = String(item?.subject || "person").trim();
  const image = String(item?.image || "").trim();

  let acceptableAnswers = Array.isArray(item?.acceptable_answers)
    ? item.acceptable_answers
        .map((value) => String(value || "").trim())
        .filter(Boolean)
    : [];

  if (baseVerb && !acceptableAnswers.includes(baseVerb)) {
    acceptableAnswers.unshift(baseVerb);
  }

  if (verb && !acceptableAnswers.includes(verb)) {
    acceptableAnswers.push(verb);
  }

  acceptableAnswers = [...new Set(acceptableAnswers)];

  return {
    verb,
    base_verb: baseVerb || verb,
    acceptable_answers: acceptableAnswers,
    semantic_hint: semanticHint,
    subject,
    image,
    tags: Array.isArray(item?.tags) ? item.tags : [],
  };
}

function buildSessionItems(items) {
  const shuffled = shuffleItems(items || []);
  return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
}

export default function ActionVerbNaming() {
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

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);
  const postedSessionRef = useRef(false);
  const advanceTimeoutRef = useRef(null);
  const currentUtteranceRef = useRef(null);

  const [isMuted, setIsMuted] = useState(() => {
    try {
      return localStorage.getItem("relingo_muted_action_verb") === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem("relingo_muted_action_verb", isMuted ? "1" : "0");
    } catch {}
  }, [isMuted]);

  function stopSpeaking() {
    stopBrowserSpeech();
    currentUtteranceRef.current = null;
  }

  function speak(text, options = {}) {
    const { onEnd } = options;
    const finalText = String(text || "").trim();

    if (!finalText) {
      if (typeof onEnd === "function") onEnd();
      return;
    }

    if (isMuted || !("speechSynthesis" in window)) {
      if (typeof onEnd === "function") onEnd();
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
  }

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
    setScorePostResult(null);
    setScorePostError(null);
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
          throw new Error(data?.detail || "Failed to load action verb pack");
        }

        const resolvedMode = normaliseContentMode(
          response.headers.get("X-ReLingo-Resolved-Mode")
        );

        const items = Array.isArray(data?.items) ? data.items : [];
        const cleanedItems = items
          .map(normaliseActionVerbItem)
          .filter((item) => item.verb && item.image);

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
          setPackError(
            selectedMode === CURATED_MODE
              ? "No curated action verb items found."
              : "No personalised action verb items found."
          );
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
    clearAdvanceTimeout(advanceTimeoutRef);
    stopSpeaking();

    setCurrentIndex(nextIndex);
    setFeedback("");
    setAudioResult("");
    setError(null);
    setLockedUntilNext(false);
    setAttemptNum(1);
    setShowHint(false);
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
    const target = currentItem?.verb || "";

    setSessionPoints((points) => points + awardedPoints);

    setItemResults((prev) => [
      ...prev,
      {
        target,
        awardedPoints,
        attemptsUsed: attemptNum,
        cueShown: showHint,
        transcript: payload?.text || "",
        best_guess: payload?.matched_target || "",
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
      const message = "Didn’t catch that—try saying the action a bit more clearly.";
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    const matched = !!payload?.matched;
    const correctVerb = String(currentItem?.verb || "").trim();

    if (matched) {
      const points = pointsForAttempt(attemptNum);
      const feedbackText = `Correct! (+${points})`;

      const speechText = correctVerb
        ? `Correct. The action is ${correctVerb}. You earned ${points} point${
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
      setShowHint(true);
      setAttemptNum(2);

      const message = "Good try. Here is a hint to help you.";
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

    const reveal = correctVerb ? ` The action was: ${correctVerb}.` : "";
    const feedbackText = `Moving on. Zero points for this one.${reveal}`;
    const speechText = correctVerb
      ? `Moving on. Zero points for this one. The correct action was ${correctVerb}.`
      : "Moving on. Zero points for this one.";

    setFeedback(feedbackText);

    finalizeCurrentItem({
      awardedPoints: 0,
      payload,
      speechText,
    });
  };

  const handleUpload = async (audioBlob) => {
    if (!currentItem) return;

    const vocabValues = [];
    sessionItems.forEach((item) => {
      if (item.verb) vocabValues.push(item.verb);
      if (item.base_verb) vocabValues.push(item.base_verb);
      if (Array.isArray(item.acceptable_answers)) {
        item.acceptable_answers.forEach((answer) => {
          if (answer) vocabValues.push(answer);
        });
      }
    });

    const vocab = [...new Set(vocabValues)].join(",");
    const acceptableAnswers = (currentItem.acceptable_answers || []).join(",");
    const tags = Array.isArray(currentItem.tags) ? currentItem.tags.join(",") : "";

    const formData = new FormData();
    formData.append("audio", audioBlob, "recording.webm");
    formData.append("vocab", vocab);
    formData.append("target_verb", currentItem.verb);
    formData.append("acceptable_answers", acceptableAnswers);
    formData.append("semantic_hint", currentItem.semantic_hint || "");
    formData.append("tags", tags);
    formData.append("attempt_num", String(attemptNum));
    formData.append("cue_shown", showHint ? "1" : "0");

    try {
      const response = await fetch(`${API_BASE}/api/action-verb-eval`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error("Failed to process action verb audio");
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

  const cueLetter = !isPackLoading && !completed && currentItem
    ? getCueFromVerb(currentItem.verb)
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
      <h1 className="basic-speech-title">Action Verb Naming</h1>

      <div className="practice-section">
        <h2>Name the action</h2>
        <p>Look at the picture and say what action is happening.</p>

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
            {packError}
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
                  alt="Action verb prompt"
                  className="practice-image"
                />

                <p
                  style={{
                    marginTop: 12,
                    marginBottom: 0,
                    fontSize: 14,
                    opacity: 0.82,
                    textAlign: "center",
                  }}
                >
                  Focus on the doing word, not the object.
                </p>

                {showHint && (
                  <div
                    style={{
                      marginTop: 10,
                      padding: 10,
                      borderRadius: 10,
                      border: "1px solid rgba(255,255,255,0.15)",
                      textAlign: "left",
                    }}
                  >
                    <div>
                      <strong>Cue:</strong> starts with <strong>{cueLetter}</strong>
                    </div>

                    {currentItem.semantic_hint && (
                      <div style={{ marginTop: 8 }}>
                        <strong>Hint:</strong> {currentItem.semantic_hint}
                      </div>
                    )}
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
                  .replaceAll("—", "-")
                  .replaceAll(".", "")}`}
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