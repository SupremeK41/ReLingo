import React from 'react';
import './App.css';
import logo from './logo2.png';
import womanTree from './WomanTree2.png';
import { useNavigate, Routes, Route } from 'react-router-dom';
import AboutUs from './components/AboutUs';

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
      
      <img src={womanTree} alt="Woman and Tree Illustration" className="woman-tree-illustration" />
    </>
  );
}

function App() {
  const navigate = useNavigate();

  const handleHomeClick = () => {
    navigate('/');
  };

  const handleAboutClick = () => {
    navigate('/about');
  };

  return (
    <div className="App">
      <nav className="navbar">
        <div className="logo-container" onClick={handleHomeClick} style={{cursor: 'pointer'}}>
          <img src={logo} alt="ReLingo Logo" className="logo" />
          <h1>ReLingo</h1>
        </div>
        <div className="nav-links">
          <button onClick={handleHomeClick}>Home</button>
          <button onClick={handleAboutClick}>About Us</button>
          <button>Start Learning</button>
        </div>
      </nav>

      <Routes>
        <Route path="/" element={<MainContent />} />
        <Route path="/about" element={<AboutUs />} />
      </Routes>
    </div>
  );
}

export default App;