import cv2
import os
import numpy as np
import time
from scipy.spatial.transform import Rotation

from picamera2 import Picamera2
from libcamera import Transform

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

def rotate_point(point, euler):
    rotation = Rotation.from_euler('xyz', euler, degrees=True)
    return rotation.apply(point)

class HeadTracker:
    def __init__(self):
        self.model_points = np.array([
            (0.0,    0.035, -0.03),    # nose tip   (y down, nose closer to camera)
            (-0.030, 0.0,    0.0),     # right eye
            (0.030,  0.0,    0.0),     # left eye
            (-0.06,  0.015,  0.125),   # right ear  (farther from camera)
            (0.060,  0.015,  0.125),   # left ear
        ], dtype=np.float32)

        # self.model_points = [
        #     (0.03, -0.035, 0.03,),             # Nose tip (origin)
        #     (-0.00, 0.00, -0.0),        # Right eye
        #     (0.060, 0.00, -0.0),         # Left eye
        #     (-0.03, -0.015, -0.125),        # Right ear
        #     (0.09, -0.015, -0.125),         # Left ear
        # ]

        self.frame_width, self.frame_height, self.camera_matrix, self.dist_coeffs = read_colmap_file()

        self.confidence_threshold = 0.75
        self.default_head_position = np.array([-0.03, 0, 1])

        self.rotation = Rotation.from_euler('xyz', (-0,-5,0), degrees=True)
        self.camera_offset = (0, 0.05, 0.0728)
        self.start = time.monotonic() * 1000

    def initialize_headtracker(self):
        model_path = "ml_models/blaze_face_full_range.tflite"

        options = vision.FaceDetectorOptions(
            base_options=python.BaseOptions(
                model_asset_path=model_path
            ),
            running_mode=vision.RunningMode.VIDEO,
            min_detection_confidence=self.confidence_threshold,
        )

        self.detector = vision.FaceDetector.create_from_options(options) 

        self.camera = Picamera2()
        config = self.camera.create_preview_configuration(
            main={"size": (640, 480), "format": "BGR888"},      # see #3 for why BGR888
            sensor={"output_size": (1640, 1232), "bit_depth": 10},  # force same FOV as calibration
            transform=Transform(hflip=1, vflip=1),              # see #2
            buffer_count=4,
        )
        self.camera.configure(config)

        self.camera.start()
        time.sleep(0.5)

    def find_reprojection_error(self, model_points, image_points, rvec, tvec):
        projected, _ = cv2.projectPoints(
            model_points, rvec, tvec,
            self.camera_matrix, self.dist_coeffs
        )
        projected = projected.reshape(-1, 2)
        errors = np.linalg.norm(projected - image_points, axis=1)
        mean_error = np.mean(errors, axis=0)

        return mean_error

    def acquire_head_position(self):

        frame = self.camera.capture_array()

        if frame is None:
            print("ERROR: Failed to read from camera!")
            return None

        mp_frame = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=frame
        )

        timestamp = int(time.monotonic() * 1000)

        detection_start = time.time()
        results = self.detector.detect_for_video(mp_frame, timestamp)
        detection_end = time.time()
        #print("detection time took", detection_end - detection_start)
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
                        mean_error = self.find_reprojection_error(valid_model_points, valid_image_points, rotation_vector, translation_vector)
                        if mean_error < 60:
                            head_position = translation_vector.flatten() * (-1, -1, 1) + self.camera_offset
                        #print("head position", head_position)
                        

            except Exception as e:
                print("Error:", e)

        return head_position

