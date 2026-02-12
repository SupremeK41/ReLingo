// frontend/src/components/BasicSpeech.js
import React, { useEffect, useMemo, useRef, useState } from "react";
import "../App.css";
import "./BasicSpeech.css";

import wordPairs from "../data/wordPairs.json";

const SESSION_SIZE = 10;

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function normalize(text = "") {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// ✅ light singularization to accept plural forms like "mountains" for "mountain"
// while still rejecting substrings like "car" in "carcinerate"
function singularize(word = "") {
  if (!word) return word;
  if (word.endsWith("ies") && word.length > 3) return word.slice(0, -3) + "y"; // babies -> baby
  if (word.endsWith("es") && word.length > 2) return word.slice(0, -2);        // boxes -> box, buses -> bus
  if (word.endsWith("s") && word.length > 1) return word.slice(0, -1);         // mountains -> mountain
  return word;
}

export default function BasicSpeech() {
  const [isRecording, setIsRecording] = useState(false);
  const [audioResult, setAudioResult] = useState("");
  const [error, setError] = useState(null);

  const [currentIndex, setCurrentIndex] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [score, setScore] = useState(0);
  const [completed, setCompleted] = useState(false);

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);

  const sessionWords = useMemo(() => {
    const shuffled = shuffle(wordPairs);
    return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
  }, []);

  const currentItem = sessionWords[currentIndex];

  useEffect(() => {
    setCurrentIndex(0);
    setScore(0);
    setFeedback("");
    setAudioResult("");
    setCompleted(false);
  }, [sessionWords]);

  const checkWord = (transcribedText) => {
    const transcript = normalize(transcribedText);
    const target = normalize(currentItem?.word || "");

    console.log("🎯 Target word:", target);
    console.log("🗣️ Transcribed:", transcript);

    // Split transcript into words, singularize each, then compare to target
    const words = transcript.split(" ").filter(Boolean);
    const normalizedWords = words.map(singularize);

    const isCorrect = normalizedWords.includes(target);

    if (isCorrect) {
      setFeedback("Correct!");
      setScore((prev) => prev + 1);
    } else {
      setFeedback("Incorrect!");
    }

    setTimeout(() => {
      const nextIndex = currentIndex + 1;

      if (nextIndex < sessionWords.length) {
        setCurrentIndex(nextIndex);
        setFeedback("");
        setAudioResult("");
      } else {
        setCompleted(true);
        setFeedback("Practice completed!");
      }
    }, 1500);
  };

  const startRecording = async () => {
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
          const audioBlob = new Blob(chunksRef.current, { type: "audio/webm" });
          console.log("🎤 Blob size:", audioBlob.size);
          await handleUpload(audioBlob);
        } finally {
          stream.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
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
    const formData = new FormData();
    formData.append("audio", audioBlob, "recording.webm");

    try {
      const response = await fetch("http://localhost:8000/api/speech-to-text", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) throw new Error("Failed to process audio");

      const data = await response.json();
      console.log("✅ Whisper returned:", data.text);

      setAudioResult(data.text || "");
      checkWord(data.text || "");
      setError(null);
    } catch (e) {
      setError("Failed to process audio: " + e.message);
    }
  };

  if (!sessionWords.length) {
    return (
      <div className="basic-speech-content">
        <h1 className="basic-speech-title">Basic Speech Practice</h1>
        <p>No word pairs found. Check wordPairs.json path + contents.</p>
      </div>
    );
  }

  return (
    <div className="basic-speech-content">
      <h1 className="basic-speech-title">Basic Speech Practice</h1>

      <div className="practice-section">
        <h2>Say what you see!</h2>
        <p>Look at the image and say the word it represents.</p>

        <div style={{ marginTop: 10 }}>
          <strong>
            Progress:{" "}
            {Math.min(currentIndex + (completed ? 0 : 1), sessionWords.length)}/
            {sessionWords.length}
          </strong>
          <span style={{ marginLeft: 12 }}>
            <strong>Score:</strong> {score}
          </span>
        </div>

        <div className="practice-cards">
          <div className="practice-card">
            {!completed && currentItem && (
              <div className="image-container">
                <img
                  src={`/images/${currentItem.image}`}
                  alt={currentItem.word}
                  className="practice-image"
                />
                <p className="target-word">🎯 Target word: {currentItem.word}</p>
              </div>
            )}

            <div className="recording-section">
              <button
                onClick={isRecording ? stopRecording : startRecording}
                className={isRecording ? "recording" : ""}
                disabled={completed}
              >
                {isRecording ? "Stop Recording" : "Start Recording"}
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
                  .replace("!", "")}`}
              >
                {feedback}
              </div>
            )}

            {error && <div className="error-message">{error}</div>}

            {completed && (
              <div style={{ marginTop: 14 }}>
                <h3>Session Complete</h3>
                <p>
                  Final score: <strong>{score}</strong> / {sessionWords.length}
                </p>
                <button
                  onClick={() => window.location.reload()}
                  style={{ marginTop: 8 }}
                >
                  Start New Session
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
