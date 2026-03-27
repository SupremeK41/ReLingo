import React from 'react';
import '../App.css';
import './AboutUs.css';

function AboutUs() {
  return (
    <div className="about-content">
      <h1 className="about-title">Why use ReLingo?</h1>
      <p className="about-text">
        ReLingo is a research-driven speech therapy application designed to support individuals with mild aphasia in regaining their ability to communicate.
        The platform was developed as a solo Master's project at the University of Birmingham, with a focus on combining clinical therapy principles with modern artificial intelligence techniques.
        ReLingo brings together speech recognition, adaptive evaluation, and AI-generated therapy content to provide structured exercises that users can complete independently. By removing the need for constant therapist supervision, the system enables more frequent practice, which is critical for effective recovery.
        The application is grounded in established speech and language therapy approaches, including lexical retrieval training, cue-based naming, semantic feature analysis, and sentence repetition. These techniques are enhanced through intelligent feedback and personalised difficulty adjustment.
        The goal of ReLingo is not to replace professional therapy, but to act as a powerful supplementary tool that increases accessibility, consistency, and engagement in the rehabilitation process.
      </p>
    </div>
  );
}

export default AboutUs; 