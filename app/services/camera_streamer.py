import urllib.request
import io
import numpy as np
from PIL import Image
from app.ml_engine.feature_extractor import FaceFeatureExtractor
from app.ml_engine.classifier_benchmark import FaceClassifierBenchmark
from app.ml_engine.liveness_detector import LivenessDetector
from app.ml_engine.duration_tracker import DurationTracker


class CameraStreamProcessor:
    """
    Real-Time Camera Stream Frame Processor.
    Handles Mobile IP Webcam streams and browser frame batches.
    Executes Liveness check -> 128-D Feature Extraction -> SVM Classification -> Duration Tracking.
    """

    def __init__(self, duration_tracker=None):
        self.duration_tracker = duration_tracker if duration_tracker else DurationTracker()
        self.liveness_detector = LivenessDetector()
        self.active_model_payload = FaceClassifierBenchmark.load_model()

    def reload_model(self):
        """Reloads the serialized machine learning model if retrained."""
        self.active_model_payload = FaceClassifierBenchmark.load_model()

    def fetch_ip_webcam_frame(self, stream_url):
        """
        Fetches a snapshot frame from Mobile IP Webcam.
        Supports both /shot.jpg and /video endpoints.
        """
        url = stream_url.strip()
        if url.endswith('/video'):
            url = url.replace('/video', '/shot.jpg')
        elif not url.endswith('/shot.jpg'):
            url = url.rstrip('/') + '/shot.jpg'

        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as response:
            img_bytes = response.read()
            return Image.open(io.BytesIO(img_bytes)).convert('RGB')

    def process_frame(self, image_input, subject_id='student_1'):
        """
        Processes a single video frame:
        1. Checks image quality & sharpness
        2. Evaluates anti-spoof liveness
        3. Extracts 128-D HOG features
        4. Predicts identity using trained SVM/KNN model
        5. Updates the 10-minute duration sampling buffer
        """
        if self.active_model_payload is None:
            self.reload_model()

        # 1. Quality & Blur check
        is_valid, quality_score, quality_msg = FaceFeatureExtractor.check_image_quality(image_input)
        if not is_valid:
            return {
                'success': False,
                'name': 'Low Quality Frame',
                'confidence': 0.0,
                'is_unknown': True,
                'message': quality_msg
            }

        # 2. Anti-spoofing / Liveness check
        is_live, liveness_score, liveness_msg = self.liveness_detector.check_liveness(image_input, subject_id)
        if not is_live:
            return {
                'success': True,
                'name': '⚠️ Spoof Detected',
                'confidence': 0.0,
                'is_unknown': True,
                'is_spoof': True,
                'message': liveness_msg
            }

        # 3. 128-D Face Feature Extraction
        emb = FaceFeatureExtractor.extract_face_embedding(image_input)

        # 4. Identity Prediction (using syllabus classifier)
        name, confidence, is_unknown = FaceClassifierBenchmark.predict_identity(
            emb, model_payload=self.active_model_payload
        )

        # 5. Log detection into Duration Tracker if recognized
        if not is_unknown and name:
            self.duration_tracker.record_detection(student_id=name, student_name=name)

        return {
            'success': True,
            'name': name,
            'confidence': confidence,
            'is_unknown': is_unknown,
            'is_spoof': False,
            'message': 'Face Recognized' if not is_unknown else 'Unregistered Person'
        }
