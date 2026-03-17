// frontend/src/components/therapySessionUtils.js
export const API_BASE = "http://localhost:8000";

export const DEFAULT_NO_SPEECH_PROB_THRESHOLD = 0.8;

export function shuffleItems(items) {
  const shuffled = Array.isArray(items) ? [...items] : [];

  for (let i = shuffled.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }

  return shuffled;
}

export function resolveImagePath(image) {
  const raw = String(image || "").trim();

  if (!raw) return "/placeholder.jpg";
  if (raw.startsWith("/")) return raw;

  if (
    raw.startsWith("images/") ||
    raw.startsWith("SentenceImages/") ||
    raw.startsWith("arasaac_images/") ||
    raw.startsWith("arasaac_sentence_images/") ||
    raw.startsWith("generated/")
  ) {
    return `/${raw}`;
  }

  return `/images/${raw}`;
}

export function pointsForAttempt(attemptNumber) {
  if (attemptNumber === 1) return 3;
  if (attemptNumber === 2) return 2;
  return 1;
}

export function buildProgressDisplay(currentIndex, isCompleted, totalItems) {
  const safeTotal = Math.max(0, Number(totalItems || 0));

  if (safeTotal === 0) return 0;
  if (isCompleted) return safeTotal;

  return Math.min(Math.max(0, Number(currentIndex || 0)) + 1, safeTotal);
}

export function isBackendNoSpeech(
  payload,
  noSpeechThreshold = DEFAULT_NO_SPEECH_PROB_THRESHOLD
) {
  if (!payload) return false;

  const text = String(payload.text || "").trim();
  if (text !== "") return false;

  if (payload.too_short) return true;
  if (
    typeof payload.no_speech_prob === "number" &&
    payload.no_speech_prob >= noSpeechThreshold
  ) {
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

export function safeJoinCsv(values) {
  if (!Array.isArray(values)) return "";

  return values
    .map((value) => String(value || "").trim())
    .filter(Boolean)
    .join(",");
}

export function dedupeStrings(values) {
  if (!Array.isArray(values)) return [];

  return [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))];
}

export function stopBrowserSpeech() {
  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
}

export function speakText(text, options = {}) {
  const {
    muted = false,
    rate = 0.9,
    pitch = 1,
    volume = 1,
    onEnd,
    onError,
  } = options;

  const finalText = String(text || "").trim();

  if (!finalText || muted || !("speechSynthesis" in window)) {
    if (typeof onEnd === "function") onEnd();
    return null;
  }

  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(finalText);
  utterance.rate = rate;
  utterance.pitch = pitch;
  utterance.volume = volume;

  utterance.onend = () => {
    if (typeof onEnd === "function") onEnd();
  };

  utterance.onerror = () => {
    if (typeof onError === "function") {
      onError();
      return;
    }
    if (typeof onEnd === "function") onEnd();
  };

  window.speechSynthesis.speak(utterance);
  return utterance;
}

export function clearAdvanceTimeout(timeoutRef) {
  if (timeoutRef?.current) {
    clearTimeout(timeoutRef.current);
    timeoutRef.current = null;
  }
}

export function scheduleAdvance(timeoutRef, callback, delayMs = 1200) {
  clearAdvanceTimeout(timeoutRef);

  timeoutRef.current = setTimeout(() => {
    timeoutRef.current = null;
    callback();
  }, delayMs);
}