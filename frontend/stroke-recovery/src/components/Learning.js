import React from "react";
import "../App.css";
import "./Learning.css";
import { Route, Routes, useNavigate } from "react-router-dom";

import BasicSpeech from "./BasicSpeech";
import NamingWithCues from "./NamingWithCues";
import SemanticFeatureAnalysis from "./SemanticFeatureAnalysis";
import SentenceRepetition from "./SentenceRepetition";

function Learning() {
  const navigate = useNavigate();

  return (
    <div className="learning-content">
      <h1 className="learning-title">Start Your Journey</h1>

      <div className="learning-sections">
        <div className="learning-card">
          <h2>Image Naming</h2>
          <p>Begin with simple image naming exercises</p>
          <button
            className="learning-button"
            onClick={() => navigate("/basic-speech")}
          >
            Start
          </button>
        </div>

        <div className="learning-card">
          <h2>Sentence Repetition</h2>
          <p>Practice repeating short everyday sentences</p>
          <button
            className="learning-button"
            onClick={() => navigate("/sentence-repetition")}
          >
            Start
          </button>
        </div>

        <div className="learning-card">
          <h2>Naming with Cues</h2>
          <p>Use helpful cues to support word retrieval</p>
          <button
            className="learning-button"
            onClick={() => navigate("/naming-with-cues")}
          >
            Start
          </button>
        </div>

        <div className="learning-card">
          <h2>Semantic Feature Analysis</h2>
          <p>Describe features of a word to strengthen retrieval</p>
          <button
            className="learning-button"
            onClick={() => navigate("/semantic-feature-analysis")}
          >
            Start
          </button>
        </div>
      </div>

      <Routes>
        <Route path="/basic-speech" element={<BasicSpeech />} />
        <Route path="/sentence-repetition" element={<SentenceRepetition />} />
        <Route path="/naming-with-cues" element={<NamingWithCues />} />
        <Route path="/semantic-feature-analysis" element={<SemanticFeatureAnalysis />} />
      </Routes>
    </div>
  );
}

export default Learning;