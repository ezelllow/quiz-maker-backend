# Google Sheet Formatting Examples

Copy and paste these examples into your Paper1 sheet to see the different option types in action.

## Example 1: TEXT OPTIONS (Default - Most Questions)

**What it looks like in the sheet:**

| UID | QNo | Subtopic | Difficulty | Question | Options | Answer | Diagram |
|-----|-----|----------|-----------|----------|---------|--------|---------|
| PHY-001 | Q1 | Motion | Easy | A ball moves horizontally. Which option describes... | A) constant velocity\nB) increasing velocity\nC) decreasing velocity\nD) circular motion | B | |

**Copy-paste format for Options cell:**
```
A) constant velocity
B) increasing velocity
C) decreasing velocity
D) circular motion
```

**How it renders:**
```
○ A) constant velocity
○ B) increasing velocity
○ C) decreasing velocity
○ D) circular motion
```

---

## Example 2: TABLE OPTIONS

**What it looks like in the sheet:**

| UID | QNo | Subtopic | Difficulty | Question | Options | Answer | Diagram |
|-----|-----|----------|-----------|----------|---------|--------|---------|
| PHY-ACSBR-005 | Q5 | Forces | Medium | A ball is taken from Earth to the Moon. How do mass and weight change? | TABLE:\nProperty \| Changes\nA) mass \| no change\nB) mass \| decreases\nC) weight \| no change\nD) weight \| increases | A | |

**Copy-paste format for Options cell:**
```
TABLE:
Property | Changes
A) mass | no change
B) mass | decreases
C) weight | no change
D) weight | increases
```

**OR with more columns:**
```
TABLE:
Object | Mass | Weight | On Earth
A) Ball | 5kg | 49N | Yes
B) Ball | 5kg | 8N | No
C) Box | 10kg | 98N | Yes
D) Box | 10kg | 16N | No
```

**How it renders:**
```
┌────────┬─────────────┐
│Option  │ Property | Changes
├────────┼─────────────┤
│○ A)    │ mass | no change
│○ B)    │ mass | decreases
│○ C)    │ weight | no change
│○ D)    │ weight | increases
└────────┴─────────────┘
```

**Key formatting rules:**
1. Start with `TABLE:`
2. Next line: column headers separated by `|`
3. Data rows: `A) val1 | val2`, `B) val3 | val4`, etc.
4. Use `|` (pipe) to separate columns
5. Spaces around `|` are optional (they'll be trimmed)

---

## Example 3: IMAGE OPTIONS

**What it looks like in the sheet:**

| UID | QNo | Subtopic | Difficulty | Question | Options | Answer | Diagram |
|-----|-----|----------|-----------|----------|---------|--------|---------|
| PHY-ACSBR-002 | Q2 | Pendulum | Medium | Which diagram shows the pendulum at maximum speed? | IMAGE: | B | [File ID or URL] |

**Copy-paste format for Options cell:**
```
IMAGE:
```

**Diagram cell:**
```
10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX/PHY-ACSBR-002
```
(Google Drive file ID, or a full image URL)

**How it renders:**
```
┌─────────────────────────┐
│                         │
│    [Diagram Image]      │
│  (showing all 4 options)│
│                         │
└─────────────────────────┘
○ A) Option A
○ B) Option B
○ C) Option C
○ D) Option D
```

---

## Real Examples from Your Sheet

### Example: Row PHY-ACSBR2019-P1-4E5N-003- (TEXT)

**Current format:**
```
A moving with non-uniform acceleration and then stops
B moving with non-uniform acceleration and then with uniform speed
C moving with uniform acceleration and then stops
D moving with uniform acceleration and then with uniform speed
```

**Keep as-is!** (No prefix needed)

---

### Example: Row PHY-ACSBR2019-P1-4E5N-005- (TABLE)

**Current format:**
```
A mass    | no change
B increases | no change
C no change | decreases
D no change | increases
```

**Update to:**
```
TABLE:
Property | Effect
A) mass | no change
B) increases | no change
C) no change | decreases
D) no change | increases
```

**Or better with clearer headers:**
```
TABLE:
 | Mass | Weight
A) decreases | no change
B) increases | no change
C) no change | decreases
D) no change | increases
```

---

### Example: Row PHY-ACSBR2019-P1-4E5N-002- (IMAGE)

**Current format:**
```
A Pendulum at lowest point
B Pendulum at highest point left
C Pendulum at highest point right
D Pendulum at starting angle
```

**Update to:**
```
IMAGE:
```

(Make sure Diagram column has the image with all 4 pendulum positions)

---

## Quick Copy-Paste Templates

### TEXT (Most Common)
```
A) First option text
B) Second option text
C) Third option text
D) Fourth option text
```

### TABLE (2 Columns)
```
TABLE:
Header 1 | Header 2
A) Value 1 | Value 2
B) Value 3 | Value 4
C) Value 5 | Value 6
D) Value 7 | Value 8
```

### TABLE (3 Columns)
```
TABLE:
Column A | Column B | Column C
A) Val1 | Val2 | Val3
B) Val4 | Val5 | Val6
C) Val7 | Val8 | Val9
D) Val10 | Val11 | Val12
```

### TABLE (4 Columns)
```
TABLE:
Col1 | Col2 | Col3 | Col4
A) V1 | V2 | V3 | V4
B) V5 | V6 | V7 | V8
C) V9 | V10 | V11 | V12
D) V13 | V14 | V15 | V16
```

### IMAGE
```
IMAGE:
```

---

## Common Mistakes & Fixes

### ❌ Missing TABLE: prefix
```
Bad:  Property | Value
      A) mass | no change
```
✅ Good:
```
TABLE:
Property | Value
A) mass | no change
```

### ❌ Wrong separator (tab instead of pipe)
```
Bad:  Property	Value
```
✅ Good:
```
Property | Value
```

### ❌ Letter and value not separated
```
Bad:  Amass | no change
```
✅ Good:
```
A) mass | no change
```

### ❌ IMAGE with text
```
Bad:  IMAGE:
      A) Shows pendulum at left
      B) Shows pendulum at right
```
✅ Good:
```
IMAGE:
(nothing else - image goes in Diagram column)
```

---

## Testing Your Format

### For TEXT:
- Question should have 4 lines in Options column
- Each line starts with A), B), C), or D)
- Separated by newlines

### For TABLE:
- First line: `TABLE:`
- Second line: Column headers with `|` separators
- Next 4 lines: `A) val1 | val2`, etc.
- All headers and values present

### For IMAGE:
- Options column contains: `IMAGE:`
- Diagram column has the image (file ID or URL)
- Don't include option descriptions

---

## Validation Checklist

Before submitting a quiz, verify:

- [ ] All TEXT questions have A/B/C/D options
- [ ] All TABLE questions have `TABLE:` prefix
- [ ] All TABLE questions have header row
- [ ] All TABLE questions have exactly 4 data rows (A/B/C/D)
- [ ] All IMAGE questions have `IMAGE:` in Options
- [ ] All IMAGE questions have image in Diagram column
- [ ] All questions have Answer column filled (single letter: A/B/C/D)
- [ ] No typos in TABLE column separators (should be `|`)

---

## Quick Reference

| Format | Starts With | Structure | Image Location |
|--------|------------|-----------|-----------------|
| TEXT | A) text | 4 lines, each A)/B)/C)/D) | Diagram column (optional setup) |
| TABLE | TABLE: | Header row + 4 data rows | Diagram column (optional setup) |
| IMAGE | IMAGE: | Just "IMAGE:" | Diagram column (required) |

---

## Need to Paste Multiple Examples?

Here are full row examples you can copy directly:

### TEXT Example Row
```
UID: PHY-TEST-001
QNo: 1
Subtopic: Kinematics
Difficulty: Easy
Question: Which statement is true about constant acceleration?
Options: A) Velocity is constant
B) Acceleration changes
C) Displacement increases linearly
D) Speed remains constant
Answer: C
Diagram: (leave empty)
```

### TABLE Example Row
```
UID: PHY-TEST-002
QNo: 2
Subtopic: Forces
Difficulty: Medium
Question: Compare these objects:
Options: TABLE:
Property | Object1 | Object2
A) Mass | 5kg | 10kg
B) Weight on Earth | 49N | 98N
C) Weight on Moon | 8N | 16N
D) Density | High | Low
Answer: B
Diagram: (leave empty)
```

### IMAGE Example Row
```
UID: PHY-TEST-003
QNo: 3
Subtopic: Optics
Difficulty: Hard
Question: Which diagram shows correct light ray refraction?
Options: IMAGE:
Answer: C
Diagram: [Image file ID or URL]
```

---

**Ready to update your sheet?** Start with one question of each type to test, then expand! 🚀
