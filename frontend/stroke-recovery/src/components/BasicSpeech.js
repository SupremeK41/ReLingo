// frontend/src/components/BasicSpeech.js
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@clerk/clerk-react";

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
  speakText,
  stopBrowserSpeech,
} from "./therapySessionUtils";

const SESSION_SIZE = 10;
const TECHNIQUE_ID = "image_naming";

function getCue(word) {
  if (!word) return "";
  return String(word).trim()[0]?.toUpperCase() || "";
}

export default function BasicSpeech() {
  const { getToken } = useAuth();

  const [contentSource, setContentSource] = useState("local");
  const [baseItems, setBaseItems] = useState(wordPairs);
  const [packError, setPackError] = useState(null);

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

  const [sessionSeed, setSessionSeed] = useState(0);
  const [itemResults, setItemResults] = useState([]);

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);
  const postedSessionRef = useRef(false);
  const advanceTimeoutRef = useRef(null);
  const currentUtteranceRef = useRef(null);

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

  function stopSpeaking() {
    stopBrowserSpeech();
    currentUtteranceRef.current = null;
  }

  function speak(text, options = {}) {
    let utteranceRef = null;

    utteranceRef = speakText(text, {
      muted: isMuted,
      onEnd: () => {
        if (currentUtteranceRef.current === utteranceRef) {
          currentUtteranceRef.current = null;
        }
        if (typeof options.onEnd === "function") {
          options.onEnd();
        }
      },
      onError: () => {
        if (currentUtteranceRef.current === utteranceRef) {
          currentUtteranceRef.current = null;
        }
        if (typeof options.onEnd === "function") {
          options.onEnd();
        }
      },
    });

    currentUtteranceRef.current = utteranceRef;
  }

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

        const response = await fetch(
          `${API_BASE}/api/content-pack/active/${TECHNIQUE_ID}`,
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

        const items = Array.isArray(data?.items) ? data.items : [];

        const cleanedItems = items
          .map((item) => ({
            word: String(item?.word || "").trim(),
            image: String(item?.image || "").trim(),
          }))
          .filter((item) => item.word);

        if (cleanedItems.length > 0) {
          setContentSource("pack");
          setBaseItems(cleanedItems);
        } else {
          setContentSource("local");
          setBaseItems(wordPairs);
        }
      } catch (err) {
        setPackError(String(err.message || err));
        setContentSource("local");
        setBaseItems(wordPairs);
      }
    };

    loadPack();
  }, [getToken]);

  const sessionWords = useMemo(() => {
    const shuffled = shuffleItems(baseItems || []);
    return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
  }, [sessionSeed, baseItems]);

  const currentItem = sessionWords[currentIndex];

  useEffect(() => {
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
    setShowCue(false);
    setItemResults([]);
  }, [sessionWords]);

  const resetForNextItem = (nextIndex) => {
    clearAdvanceTimeout(advanceTimeoutRef);
    stopSpeaking();

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
        cueShown: showCue,
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
      const message = "Didn’t catch that—try speaking a bit longer.";
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    const matched = !!payload?.matched;
    const targetWord = currentItem?.word || "";

    if (matched) {
      const points = pointsForAttempt(attemptNum);
      const feedbackText =
        attemptNum === 1
          ? "Correct! (+3)"
          : attemptNum === 2
          ? "Correct! (+2)"
          : "Correct after cue! (+1)";

      const speechText =
        targetWord && points > 0
          ? `Correct. The word is ${targetWord}. You earned ${points} point${
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
      setShowCue(true);
      setAttemptNum(2);

      const message = "Incorrect — here’s a cue. Try again.";
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    if (attemptNum === 2) {
      setShowCue(true);
      setAttemptNum(3);

      const message = "Still not quite — use the cue and try once more.";
      setFeedback(message);
      setLockedUntilNext(false);
      speak(message);
      return;
    }

    const feedbackText = "Incorrect. (+0)";
    const speechText = targetWord
      ? `Moving on. The correct word is ${targetWord}. Zero points for this one.`
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

    const formData = new FormData();
    formData.append("audio", audioBlob, "recording.webm");

    const vocab = sessionWords.map((item) => item.word).join(",");
    formData.append("vocab", vocab);
    formData.append("target", currentItem.word);
    formData.append("attempt_num", String(attemptNum));
    formData.append("cue_shown", showCue ? "1" : "0");

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
    if (isProcessing || completed || lockedUntilNext) return;

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
            session_size: sessionWords.length,
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
  }, [completed, sessionPoints, sessionWords.length, itemResults, getToken]);

  const resetSession = () => {
    setSessionSeed((seed) => seed + 1);
  };

  const recordDisabled = completed || isProcessing || lockedUntilNext;
  const progressDisplay = buildProgressDisplay(
    currentIndex,
    completed,
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
            <strong>Source:</strong>{" "}
            {contentSource === "pack" ? "Personalised" : "Default"}
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
            {!completed && currentItem && (
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
            )}

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