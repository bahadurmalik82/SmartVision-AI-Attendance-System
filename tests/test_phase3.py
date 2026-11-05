import os
import unittest
import base64
import io
import numpy as np
from PIL import Image, ImageFilter
from app import create_app, db
from app.models import Teacher, Student
from app.ml_engine.feature_extractor import FaceFeatureExtractor


class Phase3TestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('development')
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Path to a real reference photo in dataset
        self.real_photo_path = 'Celebrity Images/messi/0_Lionel-Messi.jpg'

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_image_quality_sharp_vs_blurry_and_dark(self):
        """Verify image quality validator passes sharp images and rejects blurry/dark images."""
        if os.path.exists(self.real_photo_path):
            sharp_img = Image.open(self.real_photo_path)
        else:
            # Fallback high-frequency checkerboard pattern
            arr = np.kron([[255, 0] * 32, [0, 255] * 32] * 32, np.ones((2, 2))).astype(np.uint8)
            sharp_img = Image.fromarray(arr)

        is_valid, score, msg = FaceFeatureExtractor.check_image_quality(sharp_img)
        self.assertTrue(is_valid, f"Sharp image failed quality check with message: {msg}")
        self.assertGreaterEqual(score, FaceFeatureExtractor.BLUR_THRESHOLD)

        # 2. Heavily blurred version
        blurry_img = sharp_img.filter(ImageFilter.GaussianBlur(radius=8))
        is_valid_b, score_b, msg_b = FaceFeatureExtractor.check_image_quality(blurry_img)
        self.assertFalse(is_valid_b)
        self.assertIn("too blurry", msg_b)

        # 3. Very dark image
        dark_img = Image.fromarray(np.ones((128, 128), dtype=np.uint8) * 15)
        is_valid_d, score_d, msg_d = FaceFeatureExtractor.check_image_quality(dark_img)
        self.assertFalse(is_valid_d)
        self.assertIn("too dark", msg_d)

    def test_face_feature_extraction_128d(self):
        """Verify feature extractor generates a normalized 128-dimensional vector."""
        if os.path.exists(self.real_photo_path):
            img = Image.open(self.real_photo_path)
        else:
            arr = np.random.randint(50, 200, (128, 128, 3), dtype=np.uint8)
            img = Image.fromarray(arr)

        emb = FaceFeatureExtractor.extract_face_embedding(img)
        self.assertEqual(emb.shape, (128,))
        # Vector should be L2-normalized
        norm = np.linalg.norm(emb)
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_average_embeddings(self):
        """Verify averaging multiple face angles produces a valid normalized 128-D vector."""
        v1 = np.ones(128, dtype=np.float32)
        v2 = np.ones(128, dtype=np.float32) * 2.0
        v3 = np.ones(128, dtype=np.float32) * 3.0

        master = FaceFeatureExtractor.average_embeddings([v1, v2, v3])
        self.assertEqual(len(master), 128)
        norm = np.linalg.norm(master)
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_student_photo_upload_endpoint(self):
        """Verify enrollment photo upload via Base64 computes and stores 128-D embedding."""
        teacher = Teacher(name="Dr. Adil Khan", email="adil@iba-suk.edu.pk")
        teacher.set_password("pass123")
        student = Student(
            roll_number="K21-0456",
            name="Zubair Ahmed",
            department="Computer Science",
            section="Section B"
        )
        db.session.add_all([teacher, student])
        db.session.commit()

        # Login
        self.client.post('/auth/login', data={'email': 'adil@iba-suk.edu.pk', 'password': 'pass123'})

        # Load real photo or high-contrast image
        if os.path.exists(self.real_photo_path):
            img = Image.open(self.real_photo_path)
        else:
            arr = np.kron([[255, 0] * 32, [0, 255] * 32] * 32, np.ones((2, 2))).astype(np.uint8)
            img = Image.fromarray(arr)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        b64_str = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode('utf-8')

        # Send 3 angle captures
        res = self.client.post(
            f'/students/upload-photos/{student.id}',
            json={'images': [b64_str, b64_str, b64_str]}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])

        # Verify student record now contains serialized 128-D face embedding
        refreshed_student = db.session.get(Student, student.id)
        embedding = refreshed_student.get_face_embedding()
        self.assertIsNotNone(embedding)
        self.assertEqual(len(embedding), 128)


if __name__ == '__main__':
    unittest.main()
