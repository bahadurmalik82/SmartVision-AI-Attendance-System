import os
import unittest
import numpy as np
from app import create_app, db
from app.models import Teacher
from app.ml_engine.classifier_benchmark import FaceClassifierBenchmark


class Phase4TestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('development')
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        self.test_models_dir = 'saved_models'
        self.benchmark = FaceClassifierBenchmark(models_dir=self.test_models_dir)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_benchmark_training_and_metrics_calculation(self):
        """Verify training candidate models (SVM, KNN, RF, LR) and computing Week 7 metrics."""
        # Generate synthetic 128-D embeddings for 3 classes
        np.random.seed(42)
        n_samples_per_class = 12
        dim = 128

        embeddings_dict = {
            'Ali Raza (K21-0145)': [np.random.normal(0.5, 0.1, dim) for _ in range(n_samples_per_class)],
            'Sara Ahmed (K21-0231)': [np.random.normal(-0.5, 0.1, dim) for _ in range(n_samples_per_class)],
            'Zubair Khan (K21-0456)': [np.random.normal(0.0, 0.1, dim) for _ in range(n_samples_per_class)]
        }

        X, y = self.benchmark.prepare_data_from_embeddings(embeddings_dict)
        self.assertEqual(X.shape, (36, 128))
        self.assertEqual(len(y), 36)

        # Run benchmark
        report = self.benchmark.train_and_benchmark(X, y, test_size=0.25, cv_folds=3)

        # 1. Verify all 4 models were evaluated
        expected_models = ['SVM (RBF Kernel)', 'K-Nearest Neighbors', 'Random Forest', 'Logistic Regression']
        for model_name in expected_models:
            self.assertIn(model_name, report['models'])
            metrics = report['models'][model_name]
            self.assertIn('accuracy', metrics)
            self.assertIn('f1_score', metrics)
            self.assertIn('precision', metrics)
            self.assertIn('recall', metrics)
            self.assertIn('cv_accuracy', metrics)
            self.assertGreaterEqual(metrics['accuracy'], 0.0)

        # 2. Verify Best Model selection
        self.assertIn(report['best_model'], expected_models)

        # 3. Verify Week 7 Confusion Matrix structure
        cm = report['confusion_matrix']
        self.assertEqual(len(cm), 3)       # 3 classes
        self.assertEqual(len(cm[0]), 3)

        # 4. Verify serialized model file exists
        saved_file = os.path.join(self.test_models_dir, 'best_face_classifier.pkl')
        self.assertTrue(os.path.exists(saved_file))

    def test_prediction_and_unknown_stranger_threshold(self):
        """Verify prediction accuracy and unknown stranger rejection threshold."""
        np.random.seed(42)
        dim = 128
        embeddings_dict = {
            'Student_A': [np.random.normal(1.0, 0.05, dim) for _ in range(10)],
            'Student_B': [np.random.normal(-1.0, 0.05, dim) for _ in range(10)]
        }
        X, y = self.benchmark.prepare_data_from_embeddings(embeddings_dict)
        self.benchmark.train_and_benchmark(X, y, test_size=0.3, cv_folds=2)

        # Test known sample (near Student A cluster)
        known_sample = np.random.normal(1.0, 0.05, dim)
        name, conf, is_unknown = FaceClassifierBenchmark.predict_identity(known_sample)
        self.assertEqual(name, 'Student_A')
        self.assertFalse(is_unknown)
        self.assertGreater(conf, 0.5)

        # Test random noise sample (simulating unregistered stranger)
        # Low confidence or outlier vector
        low_confidence_sample = np.zeros(dim, dtype=np.float32)
        name_stranger, conf_stranger, is_unknown_stranger = FaceClassifierBenchmark.predict_identity(
            low_confidence_sample
        )
        # Should either be unknown or below threshold
        self.assertTrue(is_unknown_stranger or conf_stranger <= 0.6)

    def test_analytics_benchmark_web_routes(self):
        """Verify the /analytics/benchmark and /analytics/train-model endpoints."""
        teacher = Teacher(name="Dr. Zakriya", email="zakriya@iba-suk.edu.pk")
        teacher.set_password("pass123")
        db.session.add(teacher)
        db.session.commit()

        self.client.post('/auth/login', data={'email': 'zakriya@iba-suk.edu.pk', 'password': 'pass123'})

        # GET benchmark view
        res = self.client.get('/analytics/benchmark')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Machine Learning Model Benchmark', res.data)

        # POST train-model on classroom dataset
        res_train = self.client.post('/analytics/train-model', data={'source': 'dataset'}, follow_redirects=True)
        self.assertEqual(res_train.status_code, 200)
        self.assertIn(b'ML Model training complete', res_train.data)


if __name__ == '__main__':
    unittest.main()
