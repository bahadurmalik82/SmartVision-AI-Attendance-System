"""Early defaulter predictor (Phase 6).

Predicts the risk that a student falls below the 75% university exam
eligibility threshold, using Logistic Regression on per-student
attendance aggregates:

    features = [avg_attendance_percentage, total_classes,
                avg_punctuality_minutes, late_session_count]

With fewer than 10 students, or only one outcome class in the data,
Logistic Regression cannot be trained honestly, so we fall back to the
transparent rule used by the university policy and say so in the
output. Never crashes on tiny demo data.
"""

import numpy as np

THRESHOLD = 75.0


def _rule_prediction(avg_percentage):
    """Rule-based risk used when there is too little data to train on."""
    if avg_percentage < 70.0:
        return 0.90, "Barred from Exam", "rule-based"
    if avg_percentage < 75.0:
        return 0.75, "Warning (70-74%)", "rule-based"
    if avg_percentage < 90.0:
        return 0.30, "On Track", "rule-based"
    return 0.05, "Safe", "rule-based"


def build_features(student_rows):
    """Normalise raw per-student aggregates into feature dicts."""
    features = []
    for row in student_rows or []:
        features.append({
            "student_id": row.get("student_id"),
            "roll_number": row.get("roll_number", ""),
            "name": row.get("name", ""),
            "avg_percentage": float(row.get("avg_percentage", row.get("attendance_percentage", 0.0)) or 0.0),
            "total_classes": int(row.get("total_classes", 0) or 0),
            "avg_punctuality_minutes": float(row.get("avg_punctuality_minutes") or 0.0),
            "late_count": int(row.get("late_count", 0) or 0),
        })
    return features


def _feature_matrix(features):
    return np.array(
        [[f["avg_percentage"], f["total_classes"],
          f["avg_punctuality_minutes"], f["late_count"]] for f in features],
        dtype=float,
    )


def predict_defaulters(student_rows):
    """Predict at-risk students. Returns a dashboard-ready result dict.

    Returns:
        {
            "method": "logistic-regression" | "rule-based",
            "note": str explaining the method used,
            "predictions": [ {student_id, roll_number, name, avg_percentage,
                              total_classes, risk_probability, risk_label,
                              at_risk: bool}, ... ]  (sorted: highest risk first),
        }
    """
    features = build_features(student_rows)
    if not features:
        return {
            "method": "rule-based",
            "note": "No student attendance data yet.",
            "predictions": [],
        }

    labels = np.array([1 if f["avg_percentage"] < THRESHOLD else 0 for f in features])
    use_model = len(features) >= 10 and len(np.unique(labels)) >= 2

    predictions = []
    if use_model:
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler

            X = _feature_matrix(features)
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            model = LogisticRegression(max_iter=500, random_state=42)
            model.fit(X_scaled, labels)
            # P(at-risk) = probability of class 1
            class_index = list(model.classes_).index(1)
            probs = model.predict_proba(X_scaled)[:, class_index]
            method = "logistic-regression"
            note = (
                "Logistic Regression trained on this class's own attendance "
                "history (features: avg %, total classes, avg lateness, late count)."
            )
        except Exception as exc:  # fall through to the rule below
            probs = None
            method = "rule-based"
            note = f"Model training failed ({exc}) — fell back to the 75% rule."
    else:
        probs = None
        method = "rule-based"
        note = (
            f"Only {len(features)} student(s) in the data — too few to train a "
            "reliable Logistic Regression model, so risk is scored with the "
            "university rule (<70% barred, 70-74% warning)."
        )

    for i, f in enumerate(features):
        if probs is not None:
            p = round(float(probs[i]), 2)
            if p >= 0.70:
                risk_label = "Barred from Exam" if f["avg_percentage"] < 70.0 else "High Risk"
            elif p >= 0.40:
                risk_label = "Warning (70-74%)"
            elif p >= 0.15:
                risk_label = "On Track"
            else:
                risk_label = "Safe"
        else:
            p, risk_label, _ = _rule_prediction(f["avg_percentage"])
        predictions.append({
            **f,
            "risk_probability": p,
            "risk_label": risk_label,
            "at_risk": bool(f["avg_percentage"] < THRESHOLD or p >= 0.5),
        })

    predictions.sort(key=lambda d: (-d["risk_probability"], d["avg_percentage"]))
    return {"method": method, "note": note, "predictions": predictions}
