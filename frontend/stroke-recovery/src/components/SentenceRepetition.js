// frontend/src/components/SentenceRepetition.js
import React, { useEffect, useMemo, useRef, useState } from "react";
import "../App.css";
import "./BasicSpeech.css";

import sentenceData from "../data/sentenceProduction.json";

const SESSION_SIZE = 5;
const TECHNIQUE_ID = "sentence_repetition";

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

export default function SentenceRepetition() {
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

  const [isMuted, setIsMuted] = useState(() => {
    try {
      return localStorage.getItem("rel_mute_sentence") === "1";
    } catch {
      return false;
    }
  });
  const lastSpokenRef = useRef("");

  const sessionItems = useMemo(() => {
    const shuffled = shuffle(sentenceData);
    return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
  }, [sessionSeed]);

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
    // reset when session changes
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
      setFeedback("Practice completed!");
      setLockedUntilNext(false);
    }
  };

  const finalizeItem = (scoreLevel, payload) => {
    const pts = scoreLevel > 0 ? pointsForAttempt(attemptNum) : 0;
    setSessionPoints((p) => p + pts);

    setTimeout(() => {
      nextItem();
    }, 1200);
  };

  const handleEvaluation = (payload) => {
    const scoreLevel = Number(payload?.score_level ?? 0);

    if (scoreLevel >= 2) {
      setFeedback(
        attemptNum === 1
          ? "Correct sentence! (+3)"
          : attemptNum === 2
          ? "Correct! (+2)"
          : "Correct after cue! (+1)"
      );
      finalizeItem(scoreLevel, payload);
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

    setFeedback("Incorrect. (+0)");
    finalizeItem(scoreLevel, payload);
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

    const formData = new FormData();
    formData.append("audio", blob, "recording.webm");
    formData.append("target_sentence", currentItem.sentence);
    formData.append("required_content", currentItem.required_content.join(","));
    formData.append("required_function", currentItem.required_function.join(","));

    try {
      const res = await fetch("http://localhost:8000/api/sentence-eval", {
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
          >
            {isMuted ? "🔇 Muted" : "🔊 Voice On"}
          </button>
        </div>

        <div className="practice-cards">
          <div className="practice-card">
            {!completed && currentItem && (
              <div className="image-container">
                <img
                  src={`/SentenceImages/${currentItem.image}`}
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

            {audioResult && (
              <div className="result-section">
                <h4>🗣️ You said:</h4>
                <p>{audioResult}</p>
              </div>
            )}

            {feedback && (
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