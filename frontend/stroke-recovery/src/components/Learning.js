import React from 'react';
import '../App.css';
import './Learning.css';
import { useNavigate, Routes, Route } from 'react-router-dom';
import BasicSpeech from './BasicSpeech';
import SentenceRepetition from './SentenceRepetition';
import NamingWithCues from './NamingWithCues';

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
          <h2>Image Naming</h2>
          <p>Begin with simple image naming exercises</p>
          <button className="learning-button" onClick={handleBasicSpeechClick}>Start</button>
        </div>

        <div className="learning-card">
          <h2>Sentence Repetition</h2>
          <p>Practice completing sentences with correct words</p>
          <button className="learning-button" onClick={() => navigate('/sentence-repetition')}>Start</button>
          
        </div>

        <div className="learning-card">
          <h2>Naming with Cues</h2>
          <p>Use contextual clues to help with word retrieval</p>
          <button className="learning-button" onClick={() => navigate('/naming-with-cues')}>Start</button>
        </div>
      </div>
      <Routes>
        <Route path="/basic-speech" element={<BasicSpeech/>} />
        <Route path="/sentence-repetition" element={<SentenceRepetition/>} />
        <Route path="/naming-with-cues" element={<NamingWithCues/>} />
      </Routes>
    </div>
  );
}

export default Learning; 