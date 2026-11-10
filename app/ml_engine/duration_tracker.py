from datetime import datetime, timedelta


class DurationTracker:
    """
    Classroom Presence & Engagement Engine with 10-Minute Sampling Buffer.
    Solves occlusions, writing notes, turning head, and temporary movements.
    
    The Rule:
      - Lecture is divided into 10-minute blocks.
      - If a student is detected >= 1 time in a 10-minute block,
        they are credited as present for the entire 10-minute block.
      - A student missing for an entire block (e.g. leaves for 15+ mins)
        is marked absent for that specific block.
    """

    def __init__(self, block_duration_minutes=10, late_threshold_minutes=15):
        self.block_duration_minutes = block_duration_minutes
        self.late_threshold_minutes = late_threshold_minutes
        self.session_start_time = None
        self.student_sessions = {}  # { student_id: { ... } }

    def start_session(self, start_time=None):
        """Initializes a new tracking session."""
        self.session_start_time = start_time if start_time else datetime.now()
        self.student_sessions = {}

    def get_current_block_index(self, timestamp):
        """Calculates which 10-minute block the current timestamp falls into (0, 1, 2...)."""
        if not self.session_start_time:
            return 0
        elapsed = timestamp - self.session_start_time
        elapsed_minutes = max(0.0, elapsed.total_seconds() / 60.0)
        return int(elapsed_minutes // self.block_duration_minutes)

    def record_detection(self, student_id, student_name, timestamp=None):
        """
        Logs a successful face verification for a student at a given timestamp.
        Credits attendance for the current 10-minute temporal block.
        """
        now = timestamp if timestamp else datetime.now()
        if not self.session_start_time:
            self.start_session(now)

        block_idx = self.get_current_block_index(now)

        if student_id not in self.student_sessions:
            self.student_sessions[student_id] = {
                'name': student_name,
                'first_seen': now,
                'last_seen': now,
                'attended_blocks': set([block_idx]),
                'detection_count': 1
            }
        else:
            record = self.student_sessions[student_id]
            record['last_seen'] = now
            record['attended_blocks'].add(block_idx)
            record['detection_count'] += 1

    def compute_student_summary(self, student_id, class_end_time=None, total_class_minutes=None):
        """
        Calculates final minutes present, attendance percentage, and academic status.
        """
        if student_id not in self.student_sessions:
            return {
                'minutes_present': 0.0,
                'percentage': 0.0,
                'status': 'Absent',
                'first_seen': None,
                'last_seen': None
            }

        data = self.student_sessions[student_id]
        first_seen = data['first_seen']
        last_seen = data['last_seen']
        attended_blocks_count = len(data['attended_blocks'])

        # Calculate total class duration
        if total_class_minutes is None or total_class_minutes <= 0:
            end = class_end_time if class_end_time else datetime.now()
            if self.session_start_time:
                duration_delta = end - self.session_start_time
                total_class_minutes = max(1.0, duration_delta.total_seconds() / 60.0)
            else:
                total_class_minutes = 1.0

        # Total minutes credited = blocks credited * block_duration (capped at total class minutes)
        minutes_present = min(float(total_class_minutes), float(attended_blocks_count * self.block_duration_minutes))
        percentage = round(min(100.0, (minutes_present / total_class_minutes) * 100.0), 1)

        # Status determination according to university criteria
        if percentage < 50.0:
            status = 'Absent'
        else:
            # Check punctuality: arrived more than 15 minutes after start?
            arrival_delay = (first_seen - self.session_start_time).total_seconds() / 60.0
            if arrival_delay > self.late_threshold_minutes:
                status = 'Late'
            elif percentage >= 75.0:
                status = 'Present'
            else:
                status = 'Left Early'

        return {
            'minutes_present': round(minutes_present, 1),
            'percentage': percentage,
            'status': status,
            'first_seen': first_seen,
            'last_seen': last_seen,
            'detection_count': data['detection_count']
        }
