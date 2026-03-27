import React from "react";
import "./App.css";
import logo from "./logo2.png";
import womanTree from "./WomanTree2.png";
import { useNavigate, Routes, Route } from "react-router-dom";

import AboutUs from "./components/AboutUs";
import Learning from "./components/Learning";
import BasicSpeech from "./components/BasicSpeech";
import Progress from "./components/Progress";
import SentenceRepetition from "./components/SentenceRepetition";
import Personalisation from "./components/Personalisation";
import NamingWithCues from "./components/NamingWithCues";
import SemanticFeatureAnalysis from "./components/SemanticFeatureAnalysis";
import ActionVerbNaming from "./components/ActionVerbNaming";

import {
  SignedIn,
  SignedOut,
  SignInButton,
  UserButton,
  useAuth,
} from "@clerk/clerk-react";

function MainContent() {
  const navigate = useNavigate();
  const { isSignedIn } = useAuth();

  const handleFindYourVoice = () => {
    if (isSignedIn) {
      navigate("/personalisation");
      return;
    }

    const signInTrigger = document.getElementById("home-sign-in-trigger");
    if (signInTrigger) {
      signInTrigger.click();
    }
  };

  return (
    <>
      <main className="main-content">
        <h1 className="welcome-text">Welcome to ReLingo</h1>

        <div className="cta-section">
          <button
            className="cta-button"
            onClick={handleFindYourVoice}
            type="button"
            aria-label={
              isSignedIn
                ? "Go to personalisation"
                : "Sign in to start using ReLingo"
            }
          >
            Find Your Voice
          </button>

          <SignedOut>
            <div style={{ marginTop: "12px" }}>
              <SignInButton mode="modal">
                <button
                  id="home-sign-in-trigger"
                  type="button"
                  style={{ display: "none" }}
                  aria-hidden="true"
                  tabIndex={-1}
                >
                  Sign in
                </button>
              </SignInButton>
              <p style={{ margin: 0, opacity: 0.85 }}>
                Sign in to personalise your exercises and track your progress.
              </p>
            </div>
          </SignedOut>

          <SignedIn>
            <p style={{ marginTop: "12px", marginBottom: 0, opacity: 0.85 }}>
              Start by setting your preferences, then begin your exercises.
            </p>
          </SignedIn>
        </div>

        <div className="tagline-section">
          <h2 className="tagline">Relearn, Rebuild, Reconnect</h2>
          <h3 className="sub-tagline">Your Path to Finding Your Voice Again</h3>
        </div>
      </main>

      <img
        src={womanTree}
        alt="Woman and Tree Illustration"
        className="woman-tree-illustration"
      />
    </>
  );
}

function App() {
  const navigate = useNavigate();

  const handleHomeClick = () => navigate("/");
  const handleAboutClick = () => navigate("/about");
  const handleLearningClick = () => navigate("/learning");
  const handleProgressClick = () => navigate("/progress");
  const handlePersonalisationClick = () => navigate("/personalisation");

  return (
    <div className="App">
      <nav className="navbar">
        <div
          className="logo-container"
          onClick={handleHomeClick}
          style={{ cursor: "pointer" }}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              handleHomeClick();
            }
          }}
        >
          <img src={logo} alt="ReLingo Logo" className="logo" />
          <h1>ReLingo</h1>
        </div>

        <div
          className="nav-links"
          style={{ display: "flex", alignItems: "center", gap: 10 }}
        >
          <button onClick={handleHomeClick} type="button">
            Home
          </button>
          <button onClick={handleAboutClick} type="button">
            About Us
          </button>
          <button onClick={handleLearningClick} type="button">
            Start Learning
          </button>
          <button onClick={handleProgressClick} type="button">
            Progress
          </button>
          <button onClick={handlePersonalisationClick} type="button">
            Personalisation
          </button>

          <SignedOut>
            <SignInButton mode="modal">
              <button type="button">Sign in</button>
            </SignInButton>
          </SignedOut>

          <SignedIn>
            <UserButton afterSignOutUrl="/" />
          </SignedIn>
        </div>
      </nav>

      <Routes>
        <Route path="/" element={<MainContent />} />
        <Route path="/about" element={<AboutUs />} />
        <Route path="/learning" element={<Learning />} />
        <Route path="/basic-speech" element={<BasicSpeech />} />
        <Route path="/sentence-repetition" element={<SentenceRepetition />} />
        <Route path="/progress" element={<Progress />} />
        <Route path="/personalisation" element={<Personalisation />} />
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

export default App;