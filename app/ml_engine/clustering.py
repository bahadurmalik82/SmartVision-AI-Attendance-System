"""K-Means student clustering (Phase 6).

Segments students into 3 attendance clusters from the features
[attendance_percentage, avg_punctuality_minutes]:

    Cluster 0 - Star Attendees      (>= 90% attendance)
    Cluster 1 - Inconsistent        (75-89% attendance)
    Cluster 2 - At-Risk Defaulters  (< 75% attendance)

K-Means cluster numbers are arbitrary, so after fitting we re-label the
clusters by their mean attendance (highest attendance -> Cluster 0).
With fewer than 3 students, or fewer than 3 distinct feature points,
K-Means cannot form 3 clusters, so we fall back to the same thresholds
with a rule-based label and say so in the output. Never crashes on
tiny / empty demo data.
"""

import numpy as np

CLUSTER_LABELS = {
    0: "Star Attendees",
    1: "Inconsistent",
    2: "At-Risk Defaulters",
}

CLUSTER_DESCRIPTIONS = {
    0: "Punctual & Dedicated (>= 90% attendance)",
    1: "Moderate / Inconsistent (75-89% attendance)",
    2: "At-Risk Defaulters (< 75% attendance)",
}


def _rule_cluster(attendance_percentage):
    """Threshold label used as a fallback when K-Means cannot run."""
    if attendance_percentage >= 90.0:
        return 0
    if attendance_percentage >= 75.0:
        return 1
    return 2


def build_student_features(student_rows):
    """Normalise raw per-student aggregates into feature dicts.

    student_rows: list of dicts with keys
        student_id, roll_number, name,
        attendance_percentage, avg_punctuality_minutes (optional)
    Missing punctuality defaults to 0.0 (on time).
    """
    features = []
    for row in student_rows:
        features.append({
            "student_id": row.get("student_id"),
            "roll_number": row.get("roll_number", ""),
            "name": row.get("name", ""),
            "attendance_percentage": float(row.get("attendance_percentage") or 0.0),
            "avg_punctuality_minutes": float(row.get("avg_punctuality_minutes") or 0.0),
        })
    return features


def elbow_method(features, max_k=5):
    """Return [{k, inertia}] for k=1..max_k (capped by number of points).

    Used on the dashboard to justify k=3. Returns [] for empty input.
    """
    if not features:
        return []
    try:
        from sklearn.cluster import KMeans
    except Exception:
        return []
    X = np.array(
        [[f["attendance_percentage"], f["avg_punctuality_minutes"]] for f in features],
        dtype=float,
    )
    n = len(X)
    results = []
    for k in range(1, min(max_k, n) + 1):
        try:
            km = KMeans(n_clusters=k, n_init=10, random_state=42)
            km.fit(X)
            results.append({"k": k, "inertia": round(float(km.inertia_), 2)})
        except Exception:
            break
    return results


def cluster_students(student_rows):
    """Cluster students and return a dashboard-ready result dict.

    Returns:
        {
            "method": "kmeans" | "rule-based",
            "note": str explaining the method used,
            "assignments": [ {student_id, roll_number, name,
                              attendance_percentage, avg_punctuality_minutes,
                              cluster, cluster_label}, ... ],
            "clusters": { 0: {"label":..., "count":..., "avg_attendance":...,
                               "members":[...]}, 1: {...}, 2: {...} },
            "scatter": [ {"x": attendance %, "y": punctuality min,
                          "name":..., "roll_number":..., "cluster":...}, ... ],
            "elbow": [ {"k":..., "inertia":...}, ... ],
        }
    """
    features = build_student_features(student_rows or [])
    empty = {
        "method": "rule-based",
        "note": "No student attendance data yet — enrol students and run a class first.",
        "assignments": [],
        "clusters": {},
        "scatter": [],
        "elbow": [],
        "max_lateness": 10.0,
    }
    if not features:
        return empty

    X = np.array(
        [[f["attendance_percentage"], f["avg_punctuality_minutes"]] for f in features],
        dtype=float,
    )
    distinct = len(np.unique(X, axis=0))
    elbow = elbow_method(features)

    # --- Fallback: too few / too similar points for 3 real clusters ---
    if len(features) < 3 or distinct < 3:
        assignments = []
        for f in features:
            c = _rule_cluster(f["attendance_percentage"])
            assignments.append({**f, "cluster": c, "cluster_label": CLUSTER_LABELS[c]})
        return {
            "method": "rule-based",
            "note": (
                f"Only {len(features)} student(s) with {distinct} distinct "
                "attendance pattern(s) — too few for K-Means, so students were "
                "grouped by the 90% / 75% thresholds instead."
            ),
            "assignments": assignments,
            "clusters": _summarise(assignments),
            "scatter": _scatter(assignments),
            "elbow": elbow,
            "max_lateness": max(10.0, max(a["avg_punctuality_minutes"] for a in assignments)),
        }

    # --- Real K-Means (features scaled: % and minutes live on different scales) ---
    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        km = KMeans(n_clusters=3, n_init=10, random_state=42)
        raw_labels = km.fit_predict(X_scaled)
    except Exception as exc:  # never crash the analytics page
        assignments = []
        for f in features:
            c = _rule_cluster(f["attendance_percentage"])
            assignments.append({**f, "cluster": c, "cluster_label": CLUSTER_LABELS[c]})
        return {
            "method": "rule-based",
            "note": f"K-Means failed ({exc}) — fell back to the 90% / 75% thresholds.",
            "assignments": assignments,
            "clusters": _summarise(assignments),
            "scatter": _scatter(assignments),
            "elbow": elbow,
            "max_lateness": max(10.0, max(a["avg_punctuality_minutes"] for a in assignments)),
        }

    # Re-label clusters by mean attendance (highest -> Cluster 0 Star, ...).
    cluster_attendance = {}
    for label in range(3):
        members = X[raw_labels == label]
        cluster_attendance[label] = float(np.mean(members[:, 0])) if len(members) else 0.0
    ordered = sorted(cluster_attendance, key=lambda lab: cluster_attendance[lab], reverse=True)
    remap = {old: new for new, old in enumerate(ordered)}

    assignments = []
    for f, raw in zip(features, raw_labels):
        c = remap[int(raw)]
        assignments.append({**f, "cluster": c, "cluster_label": CLUSTER_LABELS[c]})

    return {
        "method": "kmeans",
        "note": "K-Means (k=3) on [attendance %, avg punctuality minutes], scaled, clusters ordered by mean attendance.",
        "assignments": assignments,
        "clusters": _summarise(assignments),
        "scatter": _scatter(assignments),
        "elbow": elbow,
        "max_lateness": max(10.0, max(a["avg_punctuality_minutes"] for a in assignments)),
    }


def _summarise(assignments):
    clusters = {}
    for c in (0, 1, 2):
        members = [a for a in assignments if a["cluster"] == c]
        if not members:
            continue
        clusters[c] = {
            "label": CLUSTER_LABELS[c],
            "description": CLUSTER_DESCRIPTIONS[c],
            "count": len(members),
            "avg_attendance": round(
                sum(m["attendance_percentage"] for m in members) / len(members), 1
            ),
            "members": [m["name"] for m in members],
        }
    return clusters


def _scatter(assignments):
    return [
        {
            "x": a["attendance_percentage"],
            "y": a["avg_punctuality_minutes"],
            "name": a["name"],
            "roll_number": a["roll_number"],
            "cluster": a["cluster"],
        }
        for a in assignments
    ]
