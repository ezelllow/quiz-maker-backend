# Backend Requirements for Quiz History Review

## Issue: History Review not showing user answers

When a quiz is submitted via `/api/quiz/submit`, the backend receives:
- `user_answers`: dict mapping question index to user's answer text
- `questions`: array with question data and correct answers

## Required Fix in quiz_backend.py

In the quiz submission endpoint (around line 1528-1560), when storing `questions_data`, each question must include:

```python
{
    "uid": question.uid,
    "index": idx,
    "question_text": question.question_text,
    "correct_answer": question.answer,  # or "answer" field
    "user_answer": user_answers.get(str(idx), None),  # CRITICAL: Store user's answer
    "is_correct": user_answers.get(str(idx), "").strip() == question.answer.strip(),
    "subtopic": question.subtopic,
    "difficulty": question.difficulty
}
```

## Current Issue
The `user_answer` field is likely missing from the stored questions_data, which is why the History review page can't display what the user answered.

## Action Required
Update the quiz submission handler to store the user's answer for each question in the questions_data JSON before saving to database. This is essential for the review feature to work.

## Files to Check
- `quiz_backend.py` - Quiz submission endpoint (POST /api/quiz/submit around line 1528-1560)
- Ensure user_answers are being matched by index and stored in each question object
