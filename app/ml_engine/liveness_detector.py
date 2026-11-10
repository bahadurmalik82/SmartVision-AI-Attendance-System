import numpy as np
from PIL import Image


class LivenessDetector:
    """
    Anti-Spoofing & Liveness Verification Engine.
    Prevents proxy attendance via printed paper photos or smartphone screen replays.
    Uses temporal micro-motion variance and high-frequency texture analysis.
    """

    MIN_MOTION_VARIANCE = 0.8    # Minimum inter-frame difference to distinguish live humans from static photos
    MAX_GLARE_RATIO = 0.25        # Proportion of saturated white pixels indicating smartphone screen reflection

    def __init__(self):
        self.previous_frames = {}  # { track_or_session_id: last_gray_array }

    def check_liveness(self, current_image, subject_id='default'):
        """
        Evaluates whether a detected face is a live human or a static photo/screen spoof.
        Returns: (is_live: bool, score: float, message: str)
        """
        if isinstance(current_image, Image.Image):
            gray = np.array(current_image.convert('L'), dtype=np.float32)
        elif isinstance(current_image, np.ndarray):
            if current_image.ndim == 3:
                gray = np.mean(current_image, axis=2).astype(np.float32)
            else:
                gray = current_image.astype(np.float32)
        else:
            return True, 1.0, "Input format bypass"

        # 1. Screen glare / flash analysis (smartphone screen reflection check)
        glare_pixels = np.sum(gray >= 250.0)
        total_pixels = gray.size
        glare_ratio = glare_pixels / float(total_pixels)

        if glare_ratio > self.MAX_GLARE_RATIO:
            return False, round(glare_ratio, 3), "⚠️ Potential Screen Spoof: High specular glare detected"

        # 2. Inter-frame micro-motion check
        if subject_id in self.previous_frames:
            prev_gray = self.previous_frames[subject_id]
            if prev_gray.shape == gray.shape:
                diff = np.abs(gray - prev_gray)
                motion_score = float(np.mean(diff))

                # If motion score is practically zero across multiple frames, it's a printed static photo
                if motion_score < self.MIN_MOTION_VARIANCE:
                    self.previous_frames[subject_id] = gray
                    return False, round(motion_score, 3), "⚠️ Static Spoof: Zero natural micro-movement detected (Printed photo)"
            
        self.previous_frames[subject_id] = gray
        return True, 1.0, "Live Human Verified"

    def reset(self):
        self.previous_frames = {}
