import os
import json
import pickle
import numpy as np
from sklearn.svm import SVC
from sklearn.neighbors import KNeighborsClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix, classification_report
from sklearn.preprocessing import LabelEncoder
from PIL import Image
from app.ml_engine.feature_extractor import FaceFeatureExtractor


class FaceClassifierBenchmark:
    """
    Syllabus-aligned Machine Learning Training & Evaluation Benchmark.
    Implements:
      - Week 4: K-Nearest Neighbors (KNN)
      - Week 5: Logistic Regression (L2 Regularized)
      - Week 6: Support Vector Machines (SVM with RBF & Linear Kernels)
      - Week 7: Accuracy, Confusion Matrix, Precision, Recall, F1-Score, Cross-Validation
      - Week 13: Random Forest Classifier
    """

    CONFIDENCE_THRESHOLD = 0.45  # Probability threshold below which face is labeled 'Unknown'

    def __init__(self, models_dir='saved_models'):
        self.models_dir = models_dir
        os.makedirs(self.models_dir, exist_ok=True)
        self.label_encoder = LabelEncoder()
        self.best_model = None
        self.best_model_name = None
        self.benchmark_results = {}
        self.classes_ = []

    def prepare_data_from_embeddings(self, embeddings_dict):
        """
        Prepares X and y matrices from a dictionary of {label: [embeddings]}.
        embeddings_dict: { 'K21-0145': [[128-D vector], ...], ... }
        """
        X = []
        y = []
        for label, emb_list in embeddings_dict.items():
            for emb in emb_list:
                X.append(emb)
                y.append(label)

        X = np.array(X, dtype=np.float32)
        y = np.array(y)
        return X, y

    def load_dataset_from_folder(self, folder_path='Celebrity Images', max_per_class=20):
        """
        Loads images from class subfolders, extracts 128-D HOG features, and returns (X, y).
        Used for benchmarking against the classroom celebrity dataset.
        """
        X, y = [], []
        if not os.path.exists(folder_path):
            raise FileNotFoundError(f"Folder '{folder_path}' does not exist.")

        classes = [d for d in os.listdir(folder_path) if os.path.isdir(os.path.join(folder_path, d))]
        for cls in sorted(classes):
            cls_dir = os.path.join(folder_path, cls)
            count = 0
            for f in os.listdir(cls_dir):
                if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                    f_path = os.path.join(cls_dir, f)
                    try:
                        img = Image.open(f_path).convert('RGB')
                        emb = FaceFeatureExtractor.extract_face_embedding(img)
                        X.append(emb)
                        y.append(cls)
                        count += 1
                        if max_per_class and count >= max_per_class:
                            break
                    except Exception:
                        continue

        return np.array(X, dtype=np.float32), np.array(y)

    def train_and_benchmark(self, X, y, test_size=0.25, cv_folds=5):
        """
        Trains and benchmarks the 4 syllabus classifiers with 5-Fold Cross Validation.
        Generates Week 7 Metrics: Confusion Matrix, Precision, Recall, F1-Score.
        """
        if len(np.unique(y)) < 2:
            raise ValueError("At least 2 distinct student classes are required to train a classifier.")

        # Encode categorical labels to integers
        y_encoded = self.label_encoder.fit_transform(y)
        self.classes_ = self.label_encoder.classes_.tolist()

        # Check if dataset size allows train_test_split and cv_folds
        min_class_count = min(np.bincount(y_encoded))
        actual_cv = min(cv_folds, min_class_count) if min_class_count >= 2 else None

        if min_class_count >= 2:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y_encoded, test_size=test_size, random_state=42, stratify=y_encoded
            )
        else:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y_encoded, test_size=test_size, random_state=42
            )

        # Define Course Outline Classifiers
        candidate_models = {
            'SVM (RBF Kernel)': SVC(kernel='rbf', C=1.0, probability=True, random_state=42),
            'K-Nearest Neighbors': KNeighborsClassifier(n_neighbors=min(3, len(X_train)), weights='distance'),
            'Random Forest': RandomForestClassifier(n_estimators=50, max_depth=8, random_state=42),
            'Logistic Regression': LogisticRegression(max_iter=500, random_state=42)
        }

        results = {}
        best_f1 = -1.0

        for name, clf in candidate_models.items():
            # 1. Train classifier
            clf.fit(X_train, y_train)

            # 2. Evaluate on test split
            preds = clf.predict(X_test)
            acc = float(accuracy_score(y_test, preds))
            prec, rec, f1, _ = precision_recall_fscore_support(y_test, preds, average='weighted', zero_division=0)

            # 3. K-Fold Cross Validation (Week 7)
            cv_mean = acc
            if actual_cv and actual_cv >= 2:
                try:
                    cv_scores = cross_val_score(clf, X, y_encoded, cv=actual_cv, scoring='accuracy')
                    cv_mean = float(np.mean(cv_scores))
                except Exception:
                    cv_mean = acc

            results[name] = {
                'accuracy': round(acc * 100, 2),
                'precision': round(float(prec) * 100, 2),
                'recall': round(float(rec) * 100, 2),
                'f1_score': round(float(f1) * 100, 2),
                'cv_accuracy': round(cv_mean * 100, 2)
            }

            # Select best model based on F1-Score
            if f1 > best_f1:
                best_f1 = f1
                self.best_model = clf
                self.best_model_name = name

        # Compute Confusion Matrix for best model (Week 7)
        best_preds = self.best_model.predict(X_test)
        cm = confusion_matrix(y_test, best_preds, labels=range(len(self.classes_)))

        self.benchmark_results = {
            'models': results,
            'best_model': self.best_model_name,
            'classes': self.classes_,
            'confusion_matrix': cm.tolist(),
            'total_samples': len(X),
            'total_classes': len(self.classes_)
        }

        # Save model and artifacts
        self.save_model()
        return self.benchmark_results

    def save_model(self, filename='best_face_classifier.pkl'):
        """Serializes best trained model and metadata."""
        target_path = os.path.join(self.models_dir, filename)
        payload = {
            'model': self.best_model,
            'model_name': self.best_model_name,
            'label_encoder': self.label_encoder,
            'classes': self.classes_,
            'threshold': self.CONFIDENCE_THRESHOLD,
            'benchmark_results': self.benchmark_results
        }
        with open(target_path, 'wb') as f:
            pickle.dump(payload, f)

        # Also write results JSON for frontend display
        json_path = os.path.join(self.models_dir, 'benchmark_report.json')
        with open(json_path, 'w') as f:
            json.dump(self.benchmark_results, f, indent=2)

        return target_path

    @classmethod
    def load_model(cls, filepath='saved_models/best_face_classifier.pkl'):
        """Loads serialized face classifier and label encoder."""
        if not os.path.exists(filepath):
            return None
        with open(filepath, 'rb') as f:
            return pickle.load(f)

    @classmethod
    def predict_identity(cls, embedding_vector, model_payload=None):
        """
        Predicts identity with unknown stranger rejection threshold.
        Returns: (predicted_name: str, confidence: float, is_unknown: bool)
        """
        if model_payload is None:
            model_payload = cls.load_model()
            if model_payload is None:
                return "Unknown (Model Not Trained)", 0.0, True

        clf = model_payload['model']
        le = model_payload['label_encoder']
        threshold = model_payload.get('threshold', cls.CONFIDENCE_THRESHOLD)

        vector = np.array(embedding_vector, dtype=np.float32).reshape(1, -1)

        # Get probabilities
        if hasattr(clf, "predict_proba"):
            probs = clf.predict_proba(vector)[0]
            best_idx = int(np.argmax(probs))
            confidence = float(probs[best_idx])
        else:
            best_idx = int(clf.predict(vector)[0])
            confidence = 1.0

        if confidence < threshold:
            return "Unknown / Unregistered", round(confidence, 2), True

        student_name = le.inverse_transform([best_idx])[0]
        return student_name, round(confidence, 2), False
