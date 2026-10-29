import cv2
import time
import numpy as np
import threading

from picamera2 import Picamera2

class CameraCalibrator:
    def __init__(self, checkerboard_length=5, checkerboard_width=8):
        self.pattern_size = (checkerboard_length, checkerboard_width)
        self.obj_points = []  # 3D points in real world space
        self.img_points = []  # 2D points in image plane

        # Prepare object points based on the checkerboard pattern
        objp = np.zeros((checkerboard_length * checkerboard_width, 3), np.float32)
        objp[:, :2] = np.mgrid[0:checkerboard_length, 0:checkerboard_width].T.reshape(-1, 2)
        self.objp = objp

        self.criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)

        self.camera = Picamera2()
        config = self.camera.create_preview_configuration(
            main={"size": (1640, 1232), "format": "RGB888"}
        )
        self.camera.configure(config)

        self.camera.start()
        time.sleep(0.5)

        self.frame_dimensions = 1640, 1232

        self.display_dimensions = 800, 426

        self.density_sample_size = np.ceil(self.frame_dimensions[0] / 10)

        density_width = 10
        density_height = int(np.ceil(self.frame_dimensions[1] / self.density_sample_size))

        self.density_map = np.zeros(
            (density_height, density_width),
            dtype=np.uint16
        )

        self.latest_frame = None
        self.frame_lock = threading.Lock()

        self.stop_event = threading.Event()

    def extract_checkerboard_corners(self, image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        ret, corners = cv2.findChessboardCorners(gray, self.pattern_size, None)

        if ret:
            # Refine corner locations
            corners_refined = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), self.criteria)
            
            for corner in corners_refined:
                x, y = corner.ravel()
                density_x = int(x // self.density_sample_size)
                density_y = int(y // self.density_sample_size)
                
                if 0 <= density_x < self.density_map.shape[1] and 0 <= density_y < self.density_map.shape[0]:
                    self.density_map[density_y, density_x] += 1

            np.clip(self.density_map, 0, 4, out=self.density_map)

            return corners_refined
        else:
            return None

    def draw_checkerboard_corners(self, image):

        corners_image = image.copy()

        density_image = np.zeros_like(image)

        for index, value in np.ndenumerate(self.density_map):

            color = (0, value * 32, 0)  # Green intensity based on density
            color = np.clip(color, 0, 255)  # Ensure color values are within valid range

            density_image[int(index[0] * self.density_sample_size):int((index[0] + 1) * self.density_sample_size),
                          int(index[1] * self.density_sample_size):int((index[1] + 1) * self.density_sample_size)] = color

        for image_points in self.img_points:
            for image_point in image_points:
                point = image_point.ravel().astype(int)
                cv2.circle(corners_image, tuple(point), 5, (0, 255, 0), -1)

        corners_image = cv2.flip(corners_image, 1)
        density_image = cv2.flip(density_image, 1)
        corners_image = cv2.resize(corners_image, self.display_dimensions)
        density_image = cv2.resize(density_image, self.display_dimensions)
        black = np.zeros_like(corners_image)

        visualizer = np.vstack((density_image, corners_image, black))

        return visualizer

    def camera_worker(self):

        cv2.namedWindow("visualizer", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(
            "visualizer",
            cv2.WND_PROP_FULLSCREEN,
            cv2.WINDOW_FULLSCREEN
        )

        while not self.stop_event.is_set():
            frame = self.camera.capture_array()

            if frame is None:
                print("Failed to capture frame from camera.")
                continue

            frame = cv2.rotate(frame, cv2.ROTATE_180)

            with self.frame_lock:
                self.latest_frame = frame

            corners_frame = self.draw_checkerboard_corners(frame)

            cv2.imshow("visualizer", corners_frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.stop_event.set()
                break

        self.cap.release()
        cv2.destroyAllWindows()

    def checkerboard_extraction_worker(self):
        while not self.stop_event.is_set():
            time.sleep(1.0)

            if self.stop_event.is_set():
                break

            with self.frame_lock:
                if self.latest_frame is None:
                    continue

                frame = self.latest_frame.copy()

            corners = self.extract_checkerboard_corners(frame)

            if corners is not None:
                self.obj_points.append(self.objp)
                self.img_points.append(corners)

    def check_corner_distribution(self):
        total_distribution = self.density_map.shape[0] * self.density_map.shape[1] * 4
        current_distribution = np.sum(self.density_map)

        distribution_percentage = (current_distribution / total_distribution) * 100
        print(f"Current corner distribution: {distribution_percentage:.2f}%", end="\r", flush=True)

        if distribution_percentage >= 85:  # Threshold for sufficient distribution
            print("Enough checkerboard corners have been detected across the frame)")
            self.stop_event.set()

    def write_colmap_file(self, filename, camera_matrix, dist_coeffs):

        fx = camera_matrix[0, 0]
        fy = camera_matrix[1, 1]
        cx = camera_matrix[0, 2]
        cy = camera_matrix[1, 2]

        k1, k2, p1, p2 = dist_coeffs.ravel()[:4]
        
        with open(filename, 'w') as f:
            f.write("# Camera list with one line of data per camera:\n")
            f.write("# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")
            f.write("# Number of cameras: 1\n")

            f.write(f"0 OPENCV {self.latest_frame.shape[1]} {self.latest_frame.shape[0]} {fx} {fy} {cx} {cy} {k1} {k2} {p1} {p2}\n")


    def calibrate_camera(self):
        self.stop_event = threading.Event()

        camera_thread = threading.Thread(target=self.camera_worker)
        extraction_thread = threading.Thread(target=self.checkerboard_extraction_worker)

        camera_thread.start()
        extraction_thread.start()

        while not self.stop_event.is_set():
            time.sleep(1)
            self.check_corner_distribution()

        camera_thread.join()
        extraction_thread.join()

        if len(self.img_points) > 0:
            ret, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
                        self.obj_points,
                        self.img_points,
                        self.latest_frame.shape[:2][::-1],
                        None,
                        None
                    )
            
            self.write_colmap_file("cameras.txt", camera_matrix, dist_coeffs)
            print("Camera calibration successful.")
            print("Camera Matrix:\n", camera_matrix)
            print("Distortion Coefficients:\n", dist_coeffs)
        else:
            print("No checkerboard corners were detected. Calibration failed.")   

def main():
    calibrator = CameraCalibrator(checkerboard_length=9, checkerboard_width=6)
    calibrator.calibrate_camera()

if __name__ == "__main__":
    main()