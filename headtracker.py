import cv2
import os
import numpy as np
import time

import mediapipe as mp

from mediapipe.tasks import python
from mediapipe.tasks.python import vision

def read_colmap_file():
    with open("cameras.txt", 'r') as f:
        lines = f.readlines()

    camera_matrix = None
    dist_coeffs = None

    for line in lines:
        if line.startswith("#"):
            continue
        parts = line.strip().split()
        if len(parts) == 12:
            frame_width = int(parts[2])
            frame_height = int(parts[3])

            camera_matrix = np.array([
                [float(parts[4]), 0, float(parts[6])],
                [0, float(parts[5]), float(parts[7])],
                [0, 0, 1]
            ])
            dist_coeffs = np.array([float(x) for x in parts[8:12]])

    return frame_width, frame_height, camera_matrix, dist_coeffs

class HeadTracker:
    def __init__(self):
        self.camera_fov = 60
        self.model_points = [
            (0.0, 0.0, 0.0,),             # Nose tip (origin)
            (-0.030, 0.035, -0.030),        # Right eye
            (0.030, 0.035, -0.030),         # Left eye
            (-0.06, 0.020, -0.095),        # Right ear
            (0.060, 0.020, -0.095),         # Left ear
        ]

        self.eye_points = [np.array([-0.030, 0.035, -0.030]), np.array([0.030, 0.035, -0.030])]

        self.frame_width, self.frame_height, self.camera_matrix, self.dist_coeffs = read_colmap_file()

        self.confidence_threshold = 0.5
        self.default_head_position = np.array([-0.03, 0, 1])

        self.start = time.monotonic() * 1000

    def initialize_headtracker(self):
        model_path = "ml_models/blaze_face_full_range.tflite"

        options = vision.FaceDetectorOptions(
            base_options=python.BaseOptions(
                model_asset_path=model_path
            ),
            running_mode=vision.RunningMode.VIDEO,
            min_detection_confidence=0.5,
        )

        self.detector = vision.FaceDetector.create_from_options(options) 

        self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        time.sleep(0.5)
        
        if not self.cap.isOpened():
            print("ERROR: Camera failed to open!")
            os._exit(0)

    def acquire_head_position(self):
        ret, frame = self.cap.read()
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        mp_frame = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=frame
        )

        timestamp = int(time.monotonic() * 1000)

        results = self.detector.detect_for_video(mp_frame, timestamp)

        head_position = None

        if len(results.detections) > 0:
            face = max(
                results.detections,
                key=lambda d: d.categories[0].score,
            )

            try:
                nose = np.array([
                    face.keypoints[2].x * self.frame_width,
                    face.keypoints[2].y * self.frame_height
                ], dtype=np.float32)

                right_eye = np.array([
                    face.keypoints[0].x * self.frame_width,
                    face.keypoints[0].y * self.frame_height
                ], dtype=np.float32)

                left_eye = np.array([
                    face.keypoints[1].x * self.frame_width,
                    face.keypoints[1].y * self.frame_height
                ], dtype=np.float32)

                right_ear = np.array([
                    face.keypoints[4].x * self.frame_width,
                    face.keypoints[4].y * self.frame_height
                ], dtype=np.float32)

                left_ear = np.array([
                    face.keypoints[5].x * self.frame_width,
                    face.keypoints[5].y * self.frame_height
                ], dtype=np.float32)

                keypoints = [nose, right_eye, left_eye, right_ear, left_ear]

                valid_model_points = []
                valid_image_points = []

                for model_point, keypoint in zip(self.model_points, keypoints):
                    if keypoint is not None:
                        valid_model_points.append(model_point)
                        valid_image_points.append(keypoint[:2])

                valid_image_points = np.asarray(
                    valid_image_points,
                    dtype=np.float32
                )

                valid_model_points = np.asarray(
                    valid_model_points,
                    dtype=np.float32
                )
                
                if len(valid_model_points) > 3:
                    
                    # Solve for pose
                    success, rotation_vector, translation_vector = cv2.solvePnP(
                        valid_model_points, # 3D points of head model
                        valid_image_points, # 2D keypoints from image
                        self.camera_matrix,
                        self.dist_coeffs, 
                        flags=cv2.SOLVEPNP_SQPNP
                    )
    
                    if success:
                        head_position = translation_vector.flatten() * (-1, -1, 1)
                        

            except Exception as e:
                print("Error:", e)

        return head_position

