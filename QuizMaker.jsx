import React, { useState, useEffect } from 'react'
import './QuizMaker.css'

const API_BASE_URL = 'http://localhost:8000'

export default function QuizMaker() {
  // State for filters
  const [subtopics, setSubtopics] = useState([])
  const [difficulties, setDifficulties] = useState([])
  const [selectedSubtopic, setSelectedSubtopic] = useState('')
  const [selectedDifficulty, setSelectedDifficulty] = useState('')
  const [questionCount, setQuestionCount] = useState(5)

  // State for quiz
  const [quiz, setQuiz] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0)
  const [userAnswers, setUserAnswers] = useState({})
  const [showResults, setShowResults] = useState(false)

  // Fetch available filters on mount
  useEffect(() => {
    fetchFilters()
  }, [])

  const fetchFilters = async () => {
    try {
      const [subRes, difRes] = await Promise.all([
        fetch(`${API_BASE_URL}/api/subtopics`),
        fetch(`${API_BASE_URL}/api/difficulties`)
      ])

      if (!subRes.ok || !difRes.ok) throw new Error('Failed to fetch filters')

      const subs = await subRes.json()
      const difs = await difRes.json()

      setSubtopics(subs)
      setDifficulties(difs)
    } catch (err) {
      setError(`Error loading filters: ${err.message}`)
    }
  }

  const handleCreateQuiz = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError(null)

    try {
      const request = {
        count: parseInt(questionCount),
        ...(selectedDifficulty && { difficulty: selectedDifficulty }),
        ...(selectedSubtopic && { subtopic: selectedSubtopic })
      }

      const response = await fetch(`${API_BASE_URL}/api/quiz`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request)
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to create quiz')
      }

      const quizData = await response.json()
      setQuiz(quizData)
      setCurrentQuestionIndex(0)
      setUserAnswers({})
      setShowResults(false)
    } catch (err) {
      setError(`Error creating quiz: ${err.message}`)
    } finally {
      setLoading(false)
    }
  }

  const handleAnswerSelect = (questionIndex, answer) => {
    setUserAnswers({
      ...userAnswers,
      [questionIndex]: answer
    })
  }

  const handleNextQuestion = () => {
    if (currentQuestionIndex < quiz.questions.length - 1) {
      setCurrentQuestionIndex(currentQuestionIndex + 1)
    }
  }

  const handlePreviousQuestion = () => {
    if (currentQuestionIndex > 0) {
      setCurrentQuestionIndex(currentQuestionIndex - 1)
    }
  }

  const handleSubmitQuiz = () => {
    let correctCount = 0
    quiz.questions.forEach((question, index) => {
      const userAnswer = userAnswers[index]
      const correctAnswer = question.answer.trim()
      if (userAnswer === correctAnswer) {
        correctCount++
      }
    })

    const percentage = Math.round((correctCount / quiz.questions.length) * 100)
    setShowResults({ correctCount, percentage })
  }

  // ==================== RENDER ====================

  // Filter Selection Screen
  if (!quiz) {
    return (
      <div className="quiz-maker-container">
        <div className="filter-card">
          <h2>Create Your Quiz</h2>

          {error && <div className="error-message">{error}</div>}

          <form onSubmit={handleCreateQuiz} className="filter-form">
            <div className="form-group">
              <label>Subtopic</label>
              <select
                value={selectedSubtopic}
                onChange={(e) => setSelectedSubtopic(e.target.value)}
                className="form-select"
              >
                <option value="">📚 All Subtopics</option>
                {subtopics.map((sub) => (
                  <option key={sub} value={sub}>
                    {sub}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>Difficulty Level</label>
              <select
                value={selectedDifficulty}
                onChange={(e) => setSelectedDifficulty(e.target.value)}
                className="form-select"
              >
                <option value="">⭐ All Levels</option>
                {difficulties.map((diff) => (
                  <option key={diff} value={diff}>
                    {diff}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>Number of Questions</label>
              <input
                type="number"
                min="1"
                max="50"
                value={questionCount}
                onChange={(e) => setQuestionCount(e.target.value)}
                className="form-input"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className={`btn btn-primary ${loading ? 'loading' : ''}`}
            >
              {loading ? '⏳ Creating Quiz...' : '🚀 Create Quiz'}
            </button>
          </form>
        </div>
      </div>
    )
  }

  // Results Screen
  if (showResults) {
    const percentage = showResults.percentage
    const correctCount = showResults.correctCount
    const totalCount = quiz.questions.length

    return (
      <div className="quiz-maker-container">
        <div className="results-card">
          <h2>🎉 Quiz Complete!</h2>

          <div className="results-score">
            <div className="score-circle">
              <div className="score-number">{percentage}%</div>
              <div className="score-label">Score</div>
            </div>

            <div className="results-details">
              <p className="results-text">
                You got <strong>{correctCount}</strong> out of <strong>{totalCount}</strong> correct!
              </p>
            </div>
          </div>

          <div className="results-breakdown">
            <h3>Answer Review</h3>
            {quiz.questions.map((question, idx) => {
              const userAnswer = userAnswers[idx]
              const correctAnswer = question.answer.trim()
              const isCorrect = userAnswer === correctAnswer

              return (
                <div key={idx} className={`review-item ${isCorrect ? 'correct' : 'incorrect'}`}>
                  <div className="review-header">
                    <span className={`review-icon ${isCorrect ? '✅' : '❌'}`}>
                      {isCorrect ? '✅' : '❌'}
                    </span>
                    <span className="review-question">Question {idx + 1}: {question.subtopic}</span>
                  </div>
                  <p className="review-text">{question.question_text}</p>
                  <div className="review-answers">
                    <p><strong>Your Answer:</strong> {userAnswer || 'Not answered'}</p>
                    <p><strong>Correct Answer:</strong> {correctAnswer}</p>
                  </div>
                </div>
              )
            })}
          </div>

          <button
            onClick={() => setQuiz(null)}
            className="btn btn-primary"
          >
            ← Take Another Quiz
          </button>
        </div>
      </div>
    )
  }

  // Quiz Display Screen
  const currentQuestion = quiz.questions[currentQuestionIndex]
  const optionsArray = currentQuestion.options.split('\n').filter(opt => opt.trim())
  const isAnswered = currentQuestionIndex in userAnswers

  return (
    <div className="quiz-maker-container">
      <div className="quiz-card">
        {/* Header */}
        <div className="quiz-header">
          <h1>Quiz</h1>
          <p className="progress-text">
            Question {currentQuestionIndex + 1} of {quiz.questions.length}
          </p>
        </div>

        {/* Progress Bar */}
        <div className="progress-bar">
          <div
            className="progress-fill"
            style={{
              width: `${((currentQuestionIndex + 1) / quiz.questions.length) * 100}%`
            }}
          />
        </div>

        {/* Question */}
        <div className="question-section">
          <h2 className="question-text">{currentQuestion.question_text}</h2>

          {/* Image */}
          {currentQuestion.image_url && (
            <div className="image-container">
              <img
                src={currentQuestion.image_url}
                alt={`Question ${currentQuestion.qno}`}
                className="question-image"
                onError={(e) => {
                  e.target.style.display = 'none'
                }}
              />
            </div>
          )}

          {/* Options */}
          <div className="options-container">
            {optionsArray.map((option, idx) => {
              const optionLetter = option.charAt(0).trim()
              const optionText = option.substring(1).trim()
              const isSelected = userAnswers[currentQuestionIndex] === optionLetter

              return (
                <label
                  key={idx}
                  className={`option-label ${isSelected ? 'selected' : ''}`}
                >
                  <input
                    type="radio"
                    name={`question-${currentQuestionIndex}`}
                    value={optionLetter}
                    checked={isSelected}
                    onChange={() => handleAnswerSelect(currentQuestionIndex, optionLetter)}
                    className="option-input"
                  />
                  <span className="option-text">{optionText}</span>
                </label>
              )
            })}
          </div>

          {/* Question Meta */}
          <div className="question-meta">
            <span>📌 {currentQuestion.subtopic}</span>
            <span>⭐ {currentQuestion.difficulty}</span>
          </div>
        </div>

        {/* Navigation */}
        <div className="navigation-section">
          <button
            onClick={handlePreviousQuestion}
            disabled={currentQuestionIndex === 0}
            className="btn btn-secondary"
          >
            ← Previous
          </button>

          <span className="nav-indicator">
            {currentQuestionIndex + 1} / {quiz.questions.length}
          </span>

          {currentQuestionIndex === quiz.questions.length - 1 ? (
            <button
              onClick={handleSubmitQuiz}
              className="btn btn-success"
            >
              Submit Quiz ✓
            </button>
          ) : (
            <button
              onClick={handleNextQuestion}
              className="btn btn-secondary"
            >
              Next →
            </button>
          )}
        </div>

        {/* Reset Button */}
        <button
          onClick={() => setQuiz(null)}
          className="btn btn-outline"
        >
          Back to Filters
        </button>
      </div>
    </div>
  )
}
