import React from 'react';
import './App.css';
import logo from './logo2.png';
import womanTree from './WomanTree2.png';
import { BrowserRouter as Router, Route, Routes ,useNavigate} from 'react-router-dom';
import AboutUs from './aboutus';


function App() {
  const handleHomeClick = () => {
    window.location.reload();
  };
  const navigate = useNavigate();
  <Router>
    <Routes>
      <Route path="/" element={<App />} />
      <Route path="/aboutus" element={<AboutUs />} />
    </Routes>
  </Router>

  return (
    <div className="App">
      <nav className="navbar">
        <div className="logo-container" onClick={handleHomeClick} style={{cursor: 'pointer'}}>
          <img src={logo} alt="ReLingo Logo" className="logo" />
          <h1>ReLingo</h1>
        </div>
        <div className="nav-links">
          <button onClick={handleHomeClick}>Home</button>
          <button onClick ={() => navigate('/aboutus')}>About Us</button>
          <button>Start Learning</button>
        </div>
      </nav>

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
    </div>
  );
}

export default App;