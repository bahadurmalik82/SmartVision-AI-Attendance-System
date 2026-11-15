"""Phase 6 tests: K-Means clustering + defaulter predictor (no DB needed)."""
import unittest

from app.ml_engine.clustering import cluster_students, elbow_method
from app.ml_engine.defaulter_predictor import predict_defaulters


def _rows(specs):
    """specs: list of (roll, name, pct, punctuality)"""
    return [{
        'student_id': i + 1, 'roll_number': roll, 'name': name,
        'attendance_percentage': pct, 'avg_punctuality_minutes': punct,
    } for i, (roll, name, pct, punct) in enumerate(specs)]


SIX = _rows([
    ("K21-0145", "Ahmed Ali", 96, 2),
    ("K21-0231", "Sara Ahmed", 88, 6),
    ("K21-0456", "Zubair Khan", 82, 10),
    ("K21-0789", "Bilal Hussain", 72, 18),
    ("K21-0901", "Fatima Noor", 64, 24),
    ("K21-1123", "Usman Tariq", 45, 30),
])


class Phase6ClusteringTestCase(unittest.TestCase):
    def test_kmeans_three_clusters(self):
        result = cluster_students(SIX)
        self.assertEqual(result['method'], 'kmeans')
        self.assertEqual(len(result['assignments']), 6)
        by_name = {a['name']: a for a in result['assignments']}
        # Highest attendance -> Star (Cluster 0), lowest -> At-Risk (Cluster 2)
        self.assertEqual(by_name['Ahmed Ali']['cluster'], 0)
        self.assertEqual(by_name['Ahmed Ali']['cluster_label'], 'Star Attendees')
        self.assertEqual(by_name['Usman Tariq']['cluster'], 2)
        self.assertEqual(by_name['Usman Tariq']['cluster_label'], 'At-Risk Defaulters')
        self.assertEqual(len(result['clusters']), 3)
        self.assertEqual(len(result['scatter']), 6)
        self.assertEqual(result['scatter'][0]['x'], 96)
        self.assertEqual(result['scatter'][0]['y'], 2)

    def test_kmeans_cluster_counts_add_up(self):
        result = cluster_students(SIX)
        total = sum(c['count'] for c in result['clusters'].values())
        self.assertEqual(total, 6)

    def test_elbow_method(self):
        elbow = elbow_method(SIX, max_k=5)
        self.assertTrue(len(elbow) >= 3)
        self.assertEqual(elbow[0]['k'], 1)
        # Inertia should not increase as k grows
        inertias = [e['inertia'] for e in elbow]
        self.assertEqual(inertias, sorted(inertias, reverse=True))

    def test_clustering_fallback_too_few_students(self):
        rows = _rows([("K21-0145", "Ahmed Ali", 96, 2),
                      ("K21-1123", "Usman Tariq", 45, 30)])
        result = cluster_students(rows)
        self.assertEqual(result['method'], 'rule-based')
        by_name = {a['name']: a for a in result['assignments']}
        self.assertEqual(by_name['Ahmed Ali']['cluster'], 0)
        self.assertEqual(by_name['Usman Tariq']['cluster'], 2)

    def test_clustering_empty_input_never_crashes(self):
        result = cluster_students([])
        self.assertEqual(result['assignments'], [])
        self.assertEqual(result['clusters'], {})
        self.assertEqual(elbow_method([]), [])


class Phase6PredictorTestCase(unittest.TestCase):
    def _pred_rows(self, specs):
        return [{
            'student_id': i + 1, 'roll_number': roll, 'name': name,
            'avg_percentage': pct, 'total_classes': 5,
            'avg_punctuality_minutes': punct, 'late_count': 0,
        } for i, (roll, name, pct, punct) in enumerate(specs)]

    def test_predictor_rule_fallback_with_few_students(self):
        result = predict_defaulters(self._pred_rows([
            ("K21-0145", "Ahmed Ali", 96, 2),
            ("K21-0231", "Sara Ahmed", 88, 6),
            ("K21-0456", "Zubair Khan", 82, 10),
            ("K21-0789", "Bilal Hussain", 72, 18),
            ("K21-0901", "Fatima Noor", 64, 24),
            ("K21-1123", "Usman Tariq", 45, 30),
        ]))
        self.assertEqual(result['method'], 'rule-based')
        by_name = {p['name']: p for p in result['predictions']}
        self.assertEqual(by_name['Usman Tariq']['risk_probability'], 0.90)
        self.assertEqual(by_name['Usman Tariq']['risk_label'], 'Barred from Exam')
        self.assertTrue(by_name['Usman Tariq']['at_risk'])
        self.assertEqual(by_name['Ahmed Ali']['risk_probability'], 0.05)
        self.assertFalse(by_name['Ahmed Ali']['at_risk'])
        # Highest risk first
        self.assertEqual(result['predictions'][0]['name'], 'Usman Tariq')

    def test_predictor_trains_logistic_regression_with_enough_data(self):
        specs = []
        for i in range(6):
            specs.append((f"K21-100{i}", f"Safe Student {i}", 85 + i, 5))
        for i in range(6):
            specs.append((f"K21-200{i}", f"Risk Student {i}", 50 + i * 3, 25))
        result = predict_defaulters(self._pred_rows(specs))
        self.assertEqual(result['method'], 'logistic-regression')
        self.assertEqual(len(result['predictions']), 12)
        by_name = {p['name']: p for p in result['predictions']}
        self.assertTrue(by_name['Risk Student 0']['at_risk'])
        self.assertFalse(by_name['Safe Student 0']['at_risk'])

    def test_predictor_empty_input_never_crashes(self):
        result = predict_defaulters([])
        self.assertEqual(result['predictions'], [])


if __name__ == '__main__':
    unittest.main()
