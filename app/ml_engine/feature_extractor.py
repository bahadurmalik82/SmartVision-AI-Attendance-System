import numpy as np
from PIL import Image
from scipy.ndimage import laplace, sobel


class FaceFeatureExtractor:
    """
    Lightweight, robust feature extraction and image quality assessment.
    Uses 128-D Histogram of Oriented Gradients (HOG) and Laplacian sharpness analysis.
    Zero external GPU or heavy model dependencies - 100% cloud & Hostinger compatible.
    """
    
    BLUR_THRESHOLD = 35.0        # Minimum Laplacian variance for sharp photos
    MIN_BRIGHTNESS = 30.0        # Reject dark photos (< 30)
    MAX_BRIGHTNESS = 235.0       # Reject overexposed washed-out photos (> 235)
    TARGET_SIZE = (128, 128)     # Standardized face input dimensions

    @classmethod
    def to_grayscale_array(cls, image_input):
        """Converts PIL Image, bytes, or numpy array to a 2D float grayscale array."""
        if isinstance(image_input, Image.Image):
            img = image_input.convert('L')
        elif isinstance(image_input, np.ndarray):
            if image_input.ndim == 3:
                # RGB to Grayscale
                img = Image.fromarray(image_input.astype(np.uint8)).convert('L')
            else:
                img = Image.fromarray(image_input.astype(np.uint8))
        else:
            raise ValueError("Unsupported image input type.")
            
        resized = img.resize(cls.TARGET_SIZE, Image.Resampling.LANCZOS)
        return np.array(resized, dtype=np.float32)

    @classmethod
    def check_image_quality(cls, image_input):
        """
        Validates sharpness and lighting of a student face photo.
        Returns: (is_valid: bool, score: float, message: str)
        """
        gray = cls.to_grayscale_array(image_input)
        
        # 1. Lighting check (Mean intensity)
        mean_brightness = float(np.mean(gray))
        if mean_brightness < cls.MIN_BRIGHTNESS:
            return False, mean_brightness, "Photo is too dark. Please ensure good classroom lighting."
        if mean_brightness > cls.MAX_BRIGHTNESS:
            return False, mean_brightness, "Photo is overexposed/too bright. Please reduce glare."
            
        # 2. Sharpness check using Laplacian variance
        lap = laplace(gray)
        sharpness_score = float(np.var(lap))
        
        if sharpness_score < cls.BLUR_THRESHOLD:
            return False, sharpness_score, f"Photo is too blurry (Sharpness: {sharpness_score:.1f} < {cls.BLUR_THRESHOLD}). Please hold still and retake."
            
        return True, sharpness_score, f"Image quality verified (Sharpness: {sharpness_score:.1f})"

    @classmethod
    def compute_hog_128d(cls, gray_image):
        """
        Computes standardized 128-dimensional Histogram of Oriented Gradients (HOG).
        128x128 image divided into 4x4 spatial cells (16 cells total).
        Each 32x32 cell computes an 8-bin orientation histogram (16 cells * 8 bins = 128 features).
        """
        # Calculate horizontal and vertical gradients
        gx = sobel(gray_image, axis=1)
        gy = sobel(gray_image, axis=0)

        magnitude = np.hypot(gx, gy)
        # Orientation in range [0, 180) degrees
        orientation = np.rad2deg(np.arctan2(gy, gx)) % 180.0

        num_cells_x, num_cells_y = 4, 4
        num_bins = 8
        bin_width = 180.0 / num_bins

        cell_w = cls.TARGET_SIZE[0] // num_cells_x
        cell_h = cls.TARGET_SIZE[1] // num_cells_y

        features = []

        for cy in range(num_cells_y):
            for cx in range(num_cells_x):
                y_start = cy * cell_h
                y_end = (cy + 1) * cell_h
                x_start = cx * cell_w
                x_end = (cx + 1) * cell_w

                cell_mag = magnitude[y_start:y_end, x_start:x_end]
                cell_ori = orientation[y_start:y_end, x_start:x_end]

                # 8-bin histogram for this cell
                hist, _ = np.histogram(cell_ori, bins=num_bins, range=(0, 180), weights=cell_mag)
                features.extend(hist)

        feat_vector = np.array(features, dtype=np.float32)

        # L2 Normalization to ensure scale and illumination invariance
        norm = np.linalg.norm(feat_vector)
        if norm > 0:
            feat_vector = feat_vector / norm

        return feat_vector

    @classmethod
    def extract_face_embedding(cls, image_input):
        """Extracts normalized 128-D face embedding vector from an image."""
        gray = cls.to_grayscale_array(image_input)
        return cls.compute_hog_128d(gray)

    @classmethod
    def average_embeddings(cls, embeddings_list):
        """Averages multiple embeddings (e.g. Front, Left, Right) into a master profile."""
        if not embeddings_list:
            return None
        arr = np.array(embeddings_list, dtype=np.float32)
        mean_vec = np.mean(arr, axis=0)
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm
        return mean_vec.tolist()
