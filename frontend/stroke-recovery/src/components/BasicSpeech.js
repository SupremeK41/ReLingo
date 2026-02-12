import React, { useState, useRef } from 'react';
import '../App.css';
import './BasicSpeech.css';

import appleImage from './apple.jpg';
import cowImage from './Cow.jpg';
import noseImage from './nose.jpg';

function BasicSpeech() {
  const [isRecording, setIsRecording] = useState(false);
  const [audioResult, setAudioResult] = useState('');
  const [error, setError] = useState(null);
  const [currentPairIndex, setCurrentPairIndex] = useState(0);
  const [feedback, setFeedback] = useState('');
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);

  const imagePairs = [
    { image: appleImage, word: 'apple' },
    { image: cowImage, word: 'cow' },
    { image: noseImage, word: 'nose' }
  ];

  const checkWord = (transcribedText) => {
    const currentWord = imagePairs[currentPairIndex].word.toLowerCase();
    const transcript = transcribedText.toLowerCase();

    console.log("🎯 Target word:", currentWord);
    console.log("🗣️ Transcribed:", transcript);

    // Simple substring match (more forgiving)
    if (transcript.includes(currentWord)) {
      setFeedback('Correct!');
    } else {
      setFeedback('Incorrect!');
    }

    // Move to next pair after delay
    setTimeout(() => {
      if (currentPairIndex < imagePairs.length - 1) {
        setCurrentPairIndex(currentPairIndex + 1);
        setFeedback('');
        setAudioResult('');
      } else {
        setFeedback('Practice completed!');
      }
    }, 2000);
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorderRef.current = new MediaRecorder(stream);
      chunksRef.current = [];

      mediaRecorderRef.current.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      mediaRecorderRef.current.onstop = async () => {
        const audioBlob = new Blob(chunksRef.current, { type: 'audio/wav' });
        await handleUpload(audioBlob);
        stream.getTracks().forEach(track => track.stop()); // Release mic
      };

      mediaRecorderRef.current.start();
      setIsRecording(true);
      setError(null);
    } catch (err) {
      setError('Error accessing microphone: ' + err.message);
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
    formData.append('audio', audioBlob, 'recording.wav');
    console.log("🎤 Blob size:", audioBlob.size);


    try {
      const response = await fetch('http://localhost:8000/api/speech-to-text', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) throw new Error('Failed to process audio');

      const data = await response.json();
      console.log("✅ Whisper returned:", data.text);

      setAudioResult(data.text);
      checkWord(data.text);
      setError(null);
    } catch (error) {
      setError('Failed to process audio: ' + error.message);
    }
  };

  return (
    <div className="basic-speech-content">
      <h1 className="basic-speech-title">Basic Speech Practice</h1>
      <div className="practice-section">
        <h2>Say what you see!</h2>
        <p>Look at the image and say the word it represents</p>

        <div className="practice-cards">
          <div className="practice-card">
            <div className="image-container">
              <img
                src={imagePairs[currentPairIndex].image}
                alt={imagePairs[currentPairIndex].word}
                className="practice-image"
              />
              <p className="target-word">🎯 Target word: {imagePairs[currentPairIndex].word}</p>
            </div>

            <div className="recording-section">
              <button
                onClick={isRecording ? stopRecording : startRecording}
                className={isRecording ? 'recording' : ''}
              >
                {isRecording ? 'Stop Recording' : 'Start Recording'}
              </button>
            </div>

            {audioResult && (
              <div className="result-section">
                <h4>🗣️ You said:</h4>
                <p>{audioResult}</p>
              </div>
            )}

            {feedback && (
              <div className={`feedback-message ${feedback.toLowerCase()}`}>
                {feedback}
              </div>
            )}

            {error && (
              <div className="error-message">
                {error}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default BasicSpeech;
