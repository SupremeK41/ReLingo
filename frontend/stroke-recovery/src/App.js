import React, { useState } from "react";
import "./App.css";
import logo from "./logo2.png";
import womanTree from "./WomanTree2.png";
import { useNavigate, Routes, Route } from "react-router-dom";

import AboutUs from "./components/AboutUs";
import Learning from "./components/Learning";
import BasicSpeech from "./components/BasicSpeech";

import {
  SignedIn,
  SignedOut,
  SignInButton,
  UserButton,
  useAuth,
} from "@clerk/clerk-react";

function MainContent() {
  return (
    <>
      <main className="main-content">
        <h1 className="welcome-text">Welcome to ReLingo</h1>

        <div className="cta-section">
          <button className="cta-button">Find Your Voice</button>
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

function BackendDebug() {
  const { getToken } = useAuth();
  const [me, setMe] = useState(null);
  const [scoreRes, setScoreRes] = useState(null);
  const [error, setError] = useState(null);

  const callMe = async () => {
    setError(null);
    try {
      const token = await getToken();
      console.log("TOKEN:", token);
      console.log("TOKEN looks like JWT?", token?.split(".").length === 3);
      const res = await fetch("http://localhost:8000/api/me", {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      console.log("/api/me:", res.status, data);
      if (!res.ok) throw new Error(data?.detail || "Failed /api/me");
      setMe(data);
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const callScore = async () => {
    setError(null);
    try {
      const token = await getToken();
      console.log("TOKEN:", token);
      console.log("TOKEN looks like JWT?", token?.split(".").length === 3);
      const res = await fetch("http://localhost:8000/api/score", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ delta_score: 5 }),
      });
      const data = await res.json();
      console.log("/api/score:", res.status, data);
      if (!res.ok) throw new Error(data?.detail || "Failed /api/score");
      setScoreRes(data);
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  return (
    <div style={{ marginLeft: 12, display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ fontSize: 12, opacity: 0.85 }}>Backend debug (signed in)</div>
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={callMe} style={{ padding: "6px 10px" }}>Test /api/me</button>
        <button onClick={callScore} style={{ padding: "6px 10px" }}>Test /api/score</button>
      </div>

      {error && (
        <div style={{ fontSize: 12, color: "crimson" }}>
          {error}
        </div>
      )}

      {me && (
        <div style={{ fontSize: 12 }}>
          <strong>Me:</strong> exp={me.total_exp} streak={me.streak} best={me.best_daily_score}
        </div>
      )}

      {scoreRes && (
        <div style={{ fontSize: 12 }}>
          <strong>Score update:</strong> exp={scoreRes.total_exp} streak={scoreRes.streak} best={scoreRes.best_daily_score}
        </div>
      )}
    </div>
  );
}

function App() {
  const navigate = useNavigate();

  const handleHomeClick = () => navigate("/");
  const handleAboutClick = () => navigate("/about");
  const handleLearningClick = () => navigate("/learning");

  return (
    <div className="App">
      <nav className="navbar">
        <div
          className="logo-container"
          onClick={handleHomeClick}
          style={{ cursor: "pointer" }}
        >
          <img src={logo} alt="ReLingo Logo" className="logo" />
          <h1>ReLingo</h1>
        </div>

        <div className="nav-links" style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button onClick={handleHomeClick}>Home</button>
          <button onClick={handleAboutClick}>About Us</button>
          <button onClick={handleLearningClick}>Start Learning</button>

          <SignedOut>
            <SignInButton mode="modal">
              <button>Sign in</button>
            </SignInButton>
          </SignedOut>

          <SignedIn>
            <UserButton afterSignOutUrl="/" />
            <BackendDebug />
          </SignedIn>
        </div>
      </nav>

      <Routes>
        <Route path="/" element={<MainContent />} />
        <Route path="/about" element={<AboutUs />} />
        <Route path="/learning" element={<Learning />} />
        <Route path="/basic-speech" element={<BasicSpeech />} />
      </Routes>
    </div>
  );
}

export default App;
