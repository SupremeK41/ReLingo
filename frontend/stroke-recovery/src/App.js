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


import { SignedIn, SignedOut, SignInButton, UserButton } from "@clerk/clerk-react";

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
          <button onClick={() => navigate("/progress")}>Progress</button>
          <button onClick={() => navigate("/personalisation")}>Personalisation</button>


          <SignedOut>
            <SignInButton mode="modal">
              <button>Sign in</button>
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
        <Route path="/sentence-repetition" element={<SentenceRepetition/>} />
        <Route path="/progress" element={<Progress />} />
        <Route path="/personalisation" element={<Personalisation />} />
      </Routes>
    </div>
  );
}

export default App;
