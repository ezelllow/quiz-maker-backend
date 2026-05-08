import React from 'react'
import QuizMaker from './components/QuizMaker'
import './App.css'

function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>📚 Quiz Maker</h1>
        <p>Create personalized quizzes based on difficulty and topic</p>
      </header>

      <main className="app-main">
        <QuizMaker />
      </main>

      <footer className="app-footer">
        <p>Backend API: <code>http://localhost:8000</code></p>
      </footer>
    </div>
  )
}

export default App
