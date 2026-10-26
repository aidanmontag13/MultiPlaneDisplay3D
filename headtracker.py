import cv2
import os
import numpy as np
import time
from ultralytics import YOLO

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
            camera_matrix = np.array([
                [float(parts[4]), 0, float(parts[6])],
                [0, float(parts[5]), float(parts[7])],
                [0, 0, 1]
            ])
            dist_coeffs = np.array([float(x) for x in parts[8:12]])

    return camera_matrix, dist_coeffs

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

        self.frame_width = 320
        self.frame_height = 240

        self.camera_matrix, self.dist_coeffs = read_colmap_file()

        self.confidence_threshold = 0.5
        self.default_head_position = np.array([-0.03, 0, 1])

    def initialize_headtracker(self):
        # Load the YOLOv8n-pose model

        self.model = YOLO("yolov8n-pose.pt")

        self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        time.sleep(0.5)
        
        if not self.cap.isOpened():
            print("ERROR: Camera failed to open!")
            os._exit(0)

    def acquire_head_position(self):
        ret, frame = self.cap.read()
        frame_width = frame.shape[1]
        frame_height = frame.shape[0]
        #frame = cv2.resize(frame, (320, 320))
        results = self.model(frame, imgsz=320, conf=self.confidence_threshold, verbose=False)

        head_position = None

        if len(results[0].keypoints.data) > 0 and results[0].keypoints.conf is not None:

            try:
                kp = results[0].keypoints.data[0]
                confidences = results[0].keypoints.conf[0].cpu().numpy()

                nose = kp[0].cpu().numpy() if confidences[0] > self.confidence_threshold else None
                right_eye = kp[2].cpu().numpy() if confidences[2] > self.confidence_threshold else None
                left_eye = kp[1].cpu().numpy() if confidences[1] > self.confidence_threshold else None
                right_ear = kp[4].cpu().numpy() if confidences[4] > self.confidence_threshold else None
                left_ear = kp[3].cpu().numpy() if confidences[3] > self.confidence_threshold else None

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

