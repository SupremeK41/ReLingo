import React, { useEffect, useState } from "react";
import "../App.css";
import "./Learning.css";
import { Route, Routes, useNavigate } from "react-router-dom";

import BasicSpeech from "./BasicSpeech";
import NamingWithCues from "./NamingWithCues";
import SemanticFeatureAnalysis from "./SemanticFeatureAnalysis";
import SentenceRepetition from "./SentenceRepetition";
import ActionVerbNaming from "./ActionVerbNaming";

const PERSONALISED_MODE = "personalised";
const CURATED_MODE = "curated";
const STORAGE_KEY = "relingo_global_content_mode";

const TECHNIQUES = [
  {
    id: "image_naming",
    title: "Image Naming",
    description: "Begin with simple image naming exercises",
    route: "/basic-speech",
  },
  {
    id: "sentence_repetition",
    title: "Sentence Repetition",
    description: "Practice repeating short everyday sentences",
    route: "/sentence-repetition",
  },
  {
    id: "naming_with_cues",
    title: "Naming with Cues",
    description: "Use helpful cues to support word retrieval",
    route: "/naming-with-cues",
  },
  {
    id: "semantic_feature_analysis",
    title: "Semantic Feature Analysis",
    description: "Describe features of a word to strengthen retrieval",
    route: "/semantic-feature-analysis",
  },
  {
    id: "action_verb_naming",
    title: "Action Verb Naming",
    description: "Name the action happening in the picture",
    route: "/action-verb-naming",
  },
];

function getInitialMode() {
  const saved = localStorage.getItem(STORAGE_KEY);
  return saved === CURATED_MODE ? CURATED_MODE : PERSONALISED_MODE;
}

function Learning() {
  const navigate = useNavigate();
  const [contentMode, setContentMode] = useState(getInitialMode);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, contentMode);
  }, [contentMode]);

  const toggleMode = () => {
    setContentMode((current) =>
      current === PERSONALISED_MODE ? CURATED_MODE : PERSONALISED_MODE
    );
  };

  const handleStart = (technique) => {
    navigate(`${technique.route}?mode=${contentMode}`, {
      state: {
        contentMode,
        techniqueId: technique.id,
      },
    });
  };

  const isPersonalised = contentMode === PERSONALISED_MODE;

  return (
    <div className="learning-content">
      <h1 className="learning-title">Start Your Journey</h1>

      <div className="learning-mode-wrap">
        <button
          type="button"
          className="learning-mode-button"
          onClick={toggleMode}
        >
          Mode: {isPersonalised ? "Personalised" : "Curated"}
        </button>

        <p className="learning-mode-text">
          {isPersonalised
            ? "Using personalised packs when available, with curated fallback."
            : "Using curated library packs for all techniques."}
        </p>
      </div>

      <div className="learning-sections">
        {TECHNIQUES.map((technique) => (
          <div className="learning-card" key={technique.id}>
            <h2>{technique.title}</h2>
            <p>{technique.description}</p>
            <button
              className="learning-button"
              onClick={() => handleStart(technique)}
            >
              Start
            </button>
          </div>
        ))}
      </div>

      <Routes>
        <Route path="/basic-speech" element={<BasicSpeech />} />
        <Route path="/sentence-repetition" element={<SentenceRepetition />} />
        <Route path="/naming-with-cues" element={<NamingWithCues />} />
        <Route
          path="/semantic-feature-analysis"
          element={<SemanticFeatureAnalysis />}
        />
        <Route path="/action-verb-naming" element={<ActionVerbNaming />} />
      </Routes>
    </div>
  );
}

export default Learning;