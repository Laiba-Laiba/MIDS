# Platform 9¾ — Adaptive CAPTCHA Defense System

> **FYP Iteration 1 (Baseline Prototype)**  
> A behavioural CAPTCHA defense system designed to distinguish humans from automated AI attackers using explainable rule-based risk scoring.

---

## 📖 Project Overview

Platform 9¾ is a Flask-based web application developed as a Final Year Project (FYP). The system implements three types of CAPTCHA challenges (Text, Image, Puzzle) and pits them against automated AI solvers (EasyOCR, OpenCV, and Edge-based puzzle solvers). 

Unlike traditional CAPTCHAs that act as a simple pass/fail gate, Platform 9¾ collects **30+ behavioural telemetry signals** per attempt. It calculates a real-time **risk score (0.0 to 1.0)** to detect automation without disturbing legitimate users. The system is fully transparent — every risk score is accompanied by a list of human-readable reasons (explainability).

---

## ✨ Key Features (Iteration 1)

### 1. Three CAPTCHA Generators
- **Text CAPTCHA:** 5-character distorted alphanumeric text with configurable noise, rotation, and line distortion.
- **Image CAPTCHA:** Shape grid (circle, square, triangle, star) where the user must click a specific target.
- **Puzzle CAPTCHA:** A 2×2 tile-rearrangement puzzle using a secure Fisher-Yates shuffle.

### 2. AI Attacker Suite
- **Text Attacker:** EasyOCR reads the rendered image.
- **Image Attacker:** OpenCV contour detection and polygon approximation identify shapes.
- **Puzzle Attacker:** Edge-cost analysis reconstructs the shuffled tiles.

### 3. Explainable Risk Scoring Engine
- Calculates risk based on pointer movement, keystroke dynamics, clipboard usage, focus/blur events, and tab visibility.
- Attacker attempts are flagged with a baseline bump (≥ 0.60) to ensure automation is caught.
- Every decision is explained (e.g., *"no human-like input telemetry was present"*).

### 4. Monitoring Dashboards (8 Pages)
- **Generator & Attacker:** Real-time interaction and live solver logs.
- **Report & Evaluation:** Per-attempt and grouped statistics, compared against the L-MACD paper benchmarks.
- **Behaviour Monitor:** Raw telemetry feed showing exactly which rules fired for humans vs. attackers.
- **Session Monitor:** Session-level roll-ups (IP hash, actor split, avg/max risk).
- **Verification Dashboard:** Automated self-checks (generation validity, internal consistency, risk audit).
- **Competitor Analysis:** Signal comparison against Cloudflare Turnstile, Arkose Labs, reCAPTCHA v3, and CapSolver.

---

## 🏗️ Architecture

The system follows a standard 3-tier architecture:
1. **Presentation Layer:** Single-page Flask template (`templates/index.html`) with 8 views.
2. **Application Layer:** Flask Blueprint (`app/routes.py`) exposing 12 REST endpoints.
3. **Data Layer:** SQLite database (`instance/mids.sqlite`) with 3 tables (`visitor_sessions`, `issued_challenges`, `attempts`).

### Data Flow
`Browser → Flask API → Generators / Attacker Solver → Risk Analysis → SQLite DB → Dashboard`

---

## 🛡️ Non-Functional Requirements (NFRs)

- **NFR-P (Performance):** Challenge generation is tracked; response includes `elapsed_ms` (target: < 500ms).
- **NFR-S (Security):** Ground truth (answers, target boxes) is **never** exposed to the client. Challenges are single-use with a 120-second TTL.
- **NFR-R (Reliability):** Malformed inputs do not crash the server; all endpoints return structured JSON errors.
- **NFR-U (Usability):** Distinct error messages for expired challenges, session mismatches, and invalid inputs.
- **NFR-E (Explainability):** Every risk score includes a list of `reasons`.

---

## 🧪 Testing & Verification

We maintain a strict testing pipeline using `pytest` and `ruff`.
- **Unit Tests:** 34 tests covering generator validity, risk scoring edge cases, and NFR checks.
- **Linting:** 0 errors (ruff).
- **Continuous Integration:** GitHub Actions runs `pytest` and `ruff` on every push to `main`.

Run tests locally:
```bash
python -m pytest -q # run tests
python -m ruff check . # lint

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py        # open http://127.0.0.1:5000        
