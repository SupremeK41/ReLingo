// frontend/src/components/BasicSpeech.js
import React, { useEffect, useMemo, useRef, useState } from "react";
import "../App.css";
import "./BasicSpeech.css";

import { useAuth } from "@clerk/clerk-react";
import wordPairs from "../data/wordPairs.json";

const SESSION_SIZE = 10;
const TECHNIQUE_ID = "image_naming";

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

function singularize(word = "") {
  if (!word) return word;
  if (word.endsWith("ies") && word.length > 3) return word.slice(0, -3) + "y";
  if (word.endsWith("es") && word.length > 2) return word.slice(0, -2);
  if (word.endsWith("s") && word.length > 1) return word.slice(0, -1);
  return word;
}

function levenshtein(a = "", b = "") {
  if (a === b) return 0;
  const matrix = Array.from({ length: b.length + 1 }, () =>
    Array(a.length + 1).fill(0)
  );

  for (let i = 0; i <= a.length; i++) matrix[0][i] = i;
  for (let j = 0; j <= b.length; j++) matrix[j][0] = j;

  for (let j = 1; j <= b.length; j++) {
    for (let i = 1; i <= a.length; i++) {
      if (a[i - 1] === b[j - 1]) {
        matrix[j][i] = matrix[j - 1][i - 1];
      } else {
        matrix[j][i] = Math.min(
          matrix[j - 1][i - 1] + 1,
          matrix[j][i - 1] + 1,
          matrix[j - 1][i] + 1
        );
      }
    }
  }
  return matrix[b.length][a.length];
}

function isMatch(word, target) {
  if (!word || !target) return false;

  const w = singularize(normalize(word));
  const t = singularize(normalize(target));

  if (w === t) return true;

  const dist = levenshtein(w, t);
  const maxLen = Math.max(w.length, t.length);

  // 🔹 Relative tolerance based on word length
  let allowedDistance;

  if (maxLen <= 3) {
    allowedDistance = 1;            // very short words
  } else if (maxLen <= 5) {
    allowedDistance = 1;
  } else if (maxLen <= 8) {
    allowedDistance = 2;
  } else {
    allowedDistance = 3;            // long words tolerate more distortion
  }

  // 🔹 Also allow percentage-based tolerance (important)
  const relativeTolerance = dist / maxLen;

  return dist <= allowedDistance || relativeTolerance <= 0.25;
}


export default function BasicSpeech() {
  const { getToken } = useAuth();

  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false); // ✅ blocks double runs
  const [lockedUntilNext, setLockedUntilNext] = useState(false); // ✅ prevents recording twice for same word

  const [audioResult, setAudioResult] = useState("");
  const [error, setError] = useState(null);

  const [currentIndex, setCurrentIndex] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [score, setScore] = useState(0);
  const [completed, setCompleted] = useState(false);

  // ✅ backend response after posting session score
  const [scorePostResult, setScorePostResult] = useState(null);
  const [scorePostError, setScorePostError] = useState(null);

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);

  // ✅ Create a session "seed" we can refresh without reloading the page
  const [sessionSeed, setSessionSeed] = useState(0);

  // ✅ guard to ensure we POST once per completed session
  const postedSessionRef = useRef(false);

  const sessionWords = useMemo(() => {
    const shuffled = shuffle(wordPairs);
    return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
  }, [sessionSeed]);

  const currentItem = sessionWords[currentIndex];

  useEffect(() => {
    // reset state when session changes
    setCurrentIndex(0);
    setScore(0);
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
  }, [sessionWords]);

  const nextItem = () => {
    const nextIndex = currentIndex + 1;

    if (nextIndex < sessionWords.length) {
      setCurrentIndex(nextIndex);
      setFeedback("");
      setAudioResult("");
      setError(null);
      setLockedUntilNext(false);
    } else {
      setCompleted(true);
      setFeedback("Practice completed!");
    }
  };

  const checkWord = (transcribedText) => {
      const transcript = normalize(transcribedText);
      const target = normalize(currentItem?.word || "");

      const words = transcript.split(" ").filter(Boolean);

// ✅ check full transcript first (handles “uh apple”, “it’s apple”, etc.)
      const isCorrect =
      isMatch(transcript, target) ||
      words.some((w) => isMatch(w, target));

    if (isCorrect) {
      setFeedback("Correct!");
      setScore((prev) => prev + 1);
    } else {
      setFeedback("Incorrect!");
    }

    // ✅ short delay for clean UX transition
    setTimeout(() => {
      nextItem();
    }, 1200);
  };

  const startRecording = async () => {
    // ✅ block if processing / completed / locked
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
          setLockedUntilNext(true); // ✅ prevents recording again for same word

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
  const formData = new FormData();
  formData.append("audio", audioBlob, "recording.webm");

  // ✅ session vocabulary bias (only words in THIS session)
  const vocab = sessionWords.map((x) => x.word).join(",");
  formData.append("vocab", vocab);

  try {
    const response = await fetch("http://localhost:8000/api/speech-to-text", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) throw new Error("Failed to process audio");

    const data = await response.json();

    setAudioResult(data.text || "");
    checkWord(data.text || "");
    setError(null);
  } catch (e) {
    setError("Failed to process audio: " + e.message);
    setLockedUntilNext(false);
  }
};


  // ✅ POST the session score ONCE when session completes
  useEffect(() => {
    const postSessionScore = async () => {
      setScorePostError(null);

      try {
        const token = await getToken();
        if (!token) throw new Error("No auth token (are you signed in?)");

        const res = await fetch("http://localhost:8000/api/score", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          // ✅ exp accumulates with score (backend should do gained_exp = delta_score)
          body: JSON.stringify({
            technique_id: TECHNIQUE_ID,
            delta_score: score,
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
      postedSessionRef.current = true; // lock: only post once
      postSessionScore();
    }
  }, [completed, score, getToken]);

  const resetSession = () => {
    setSessionSeed((s) => s + 1);
  };

  if (!sessionWords.length) {
    return (
      <div className="basic-speech-content">
        <h1 className="basic-speech-title">Basic Speech Practice</h1>
        <p>No word pairs found. Check wordPairs.json path + contents.</p>
      </div>
    );
  }

  const recordDisabled = completed || isProcessing || lockedUntilNext;

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
                disabled={recordDisabled && !isRecording}
                title={
                  lockedUntilNext
                    ? "Wait for feedback / next image"
                    : isProcessing
                    ? "Processing audio..."
                    : completed
                    ? "Session completed"
                    : ""
                }
              >
                {isRecording
                  ? "Stop Recording"
                  : isProcessing
                  ? "Processing..."
                  : lockedUntilNext
                  ? "Locked..."
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

                {/* ✅ Score POST status */}
                <div style={{ marginTop: 10, fontSize: 13 }}>
                  {scorePostError && (
                    <div style={{ color: "crimson" }}>
                      Failed to save score: {scorePostError}
                    </div>
                  )}

                  {scorePostResult?.global && (
                    <div>
                      <strong>Saved.</strong> Global EXP:{" "}
                      {scorePostResult.global.total_exp} | Streak:{" "}
                      {scorePostResult.global.streak}
                    </div>
                  )}

                  {scorePostResult?.technique && (
                    <div>
                      Technique ({scorePostResult.technique.technique_id}) EXP:{" "}
                      {scorePostResult.technique.total_exp} | Best:{" "}
                      {scorePostResult.technique.best_score} | Attempts:{" "}
                      {scorePostResult.technique.attempts}
                    </div>
                  )}
                </div>

                <div className="recording-section">
                  <button onClick={resetSession} className="">
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
