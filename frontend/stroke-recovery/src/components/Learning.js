import React from 'react';
import '../App.css';
import './Learning.css';
import { useNavigate, Routes, Route } from 'react-router-dom';
import BasicSpeech from './BasicSpeech';

function Learning() {
    const navigate = useNavigate();

    const handleBasicSpeechClick = () => {
        navigate('/basic-speech');
      };

  return (
    <div className="learning-content">
      <h1 className="learning-title">Start Your Journey</h1>
      
      <div className="learning-sections">
        <div className="learning-card">
          <h2>Basic Speech</h2>
          <p>Begin with simple words and phrases</p>
          <button className="learning-button" onClick={handleBasicSpeechClick}>Start</button>
        </div>

        <div className="learning-card">
          <h2>Intermediate</h2>
          <p>Practice with sentences and descriptions</p>
          <button className="learning-button">Start</button>
        </div>

        <div className="learning-card">
          <h2>Advanced</h2>
          <p>Complex conversations and storytelling</p>
          <button className="learning-button">Start</button>
        </div>
      </div>
      <Routes>
        <Route path="/basic-speech" element={<BasicSpeech/>} />
      </Routes>
    </div>
  );
}

export default Learning; 