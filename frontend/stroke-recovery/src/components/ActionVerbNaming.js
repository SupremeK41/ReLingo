import React, { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@clerk/clerk-react";

import "../App.css";
import "./BasicSpeech.css";

const SESSION_SIZE = 10;
const TECHNIQUE_ID = "action_verb_naming";
const API_BASE = "http://localhost:8000";

function shuffle(items) {
  const shuffled = [...items];

  for (let i = shuffled.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }

  return shuffled;
}

function getCueFromVerb(verb) {
  if (!verb) return "";
  return String(verb).trim()[0]?.toUpperCase() || "";
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

  return `/${image}`;
}

function pointsForAttempt(attemptNumber) {
  if (attemptNumber === 1) return 3;
  if (attemptNumber === 2) return 2;
  return 1;
}

function isNoSpeech(payload) {
  if (!payload) return false;

  const text = (payload.text || "").trim();
  if (text !== "") return false;

  if (payload.too_short) return true;
  if (typeof payload.no_speech_prob === "number" && payload.no_speech_prob >= 0.8) {
    return true;
  }
  if (payload.prompt_leak) return true;

  if (
    payload.best_guess == null &&
    payload.distance == null &&
    payload.rel_distance == null
  ) {
    return true;
  }

  return false;
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

export default function ActionVerbNaming() {
  const { getToken } = useAuth();

  const [contentSource, setContentSource] = useState("pack");
  const [baseItems, setBaseItems] = useState([]);
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
  const [showHint, setShowHint] = useState(false);
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

  useEffect(() => {
    return () => {
      if (advanceTimeoutRef.current) {
        clearTimeout(advanceTimeoutRef.current);
      }
      if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  const toggleMute = () => {
    setIsMuted((current) => {
      const next = !current;

      if (next && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
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
          setContentSource("pack");
          setBaseItems([]);
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
          throw new Error(data?.detail || "Failed to load action verb pack");
        }

        const items = Array.isArray(data?.items) ? data.items : [];
        const cleanedItems = items
          .map(normaliseActionVerbItem)
          .filter((item) => item.verb && item.image);

        if (cleanedItems.length > 0) {
          setContentSource("pack");
          setBaseItems(cleanedItems);
        } else {
          setContentSource("pack");
          setBaseItems([]);
          setPackError("No action verb items found in active pack.");
        }
      } catch (err) {
        setPackError(String(err.message || err));
        setContentSource("pack");
        setBaseItems([]);
      }
    };

    loadPack();
  }, [getToken]);

  const sessionItems = useMemo(() => {
    const shuffled = shuffle(baseItems || []);
    return shuffled.slice(0, Math.min(SESSION_SIZE, shuffled.length));
  }, [sessionSeed, baseItems]);

  const currentItem = sessionItems[currentIndex];

  useEffect(() => {
    if (advanceTimeoutRef.current) {
      clearTimeout(advanceTimeoutRef.current);
      advanceTimeoutRef.current = null;
    }

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
  }, [sessionItems]);

  const resetForNextItem = (nextIndex) => {
    if (advanceTimeoutRef.current) {
      clearTimeout(advanceTimeoutRef.current);
      advanceTimeoutRef.current = null;
    }

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
      setCompleted(true);
      setFeedback("");
      setAudioResult("");
      setLockedUntilNext(false);
    }
  };

  const finalizeCurrentItem = ({ awardedPoints, payload }) => {
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

    if (advanceTimeoutRef.current) {
      clearTimeout(advanceTimeoutRef.current);
    }

    advanceTimeoutRef.current = setTimeout(() => {
      nextItem();
    }, 1400);
  };

  const handleEvaluationResult = (payload) => {
    if (isNoSpeech(payload)) {
      setFeedback("Didn’t catch that—try saying the action a bit more clearly.");
      setLockedUntilNext(false);
      return;
    }

    const matched = !!payload?.matched;
    const correctVerb = String(currentItem?.verb || "").trim();

    if (matched) {
      const points = pointsForAttempt(attemptNum);

      setFeedback(
        attemptNum === 1
          ? `Correct: ${correctVerb}. Plus 3 points.`
          : attemptNum === 2
          ? `Correct: ${correctVerb}. Plus 2 points.`
          : `Correct: ${correctVerb}. Plus 1 point.`
      );

      finalizeCurrentItem({
        awardedPoints: points,
        payload,
      });
      return;
    }

    if (attemptNum === 1) {
      setShowHint(true);
      setAttemptNum(2);
      setFeedback("Good try. Here is a hint to help you.");
      setLockedUntilNext(false);
      return;
    }

    if (attemptNum === 2) {
      setAttemptNum(3);
      setFeedback("Try once more using the picture and the hint.");
      setLockedUntilNext(false);
      return;
    }

    const reveal = correctVerb ? ` The action was: ${correctVerb}.` : "";
    setFeedback(`Moving on. Zero points for this one.${reveal}`);

    finalizeCurrentItem({
      awardedPoints: 0,
      payload,
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
    if (isProcessing || completed || lockedUntilNext) return;

    try {
      setError(null);

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
    setSessionSeed((seed) => seed + 1);
  };

  const recordDisabled = completed || isProcessing || lockedUntilNext;
  const progressDisplay = Math.min(
    currentIndex + (completed ? 0 : 1),
    sessionItems.length
  );
  const cueLetter = !completed && currentItem ? getCueFromVerb(currentItem.verb) : "";

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

          {!completed && (
            <span style={{ marginLeft: 12 }}>
              <strong>Attempt:</strong> {attemptNum}/3
            </span>
          )}

          <span style={{ marginLeft: 12, opacity: 0.85 }}>
            <strong>Source:</strong> {contentSource === "pack" ? "Personalised" : "Default"}
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
            {!completed && currentItem && (
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
                <h4>You said:</h4>
                <p>{audioResult}</p>
              </div>
            )}

            {!completed && feedback && (
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