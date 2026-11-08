from multiplane_renderer import MultiplaneRenderer
import headtracker
from headtracker import HeadTracker
import cv2
import time
import queue
import threading
import os
import subprocess
import numpy as np
import readchar

from scipy.spatial.transform import Rotation

def write_display_calibration_file(filename, rotation):

    rotation_euler = rotation.as_euler('xyz', degrees=True)
        
    with open(filename, 'w') as f:
        f.write("# Camera list with one line of data per camera:\n")
        f.write("# CAMERA_ID, CAMERA_ROTATION_X, CAMERA_ROTATION_Y, CAMERA_ROTATION_Z\n")
        f.write("# Number of cameras: 1\n")

        f.write(f"0 {rotation_euler[0]} {rotation_euler[1]} {rotation_euler[2]}\n")

class MultiplaneCalibrator:
    def __init__(self):
        self.multiplane_renderer = MultiplaneRenderer()
        self.headtracker = HeadTracker()
        self.headtracker.initialize_headtracker()

        self.position_lock = threading.Lock()
        self.last_head_position = self.headtracker.default_head_position
        
        self.render_queue = queue.Queue(maxsize=1)

        self.display_resolution_x = 800
        self.display_resolution_y = 426
        self.display_width = 0.13596
        self.display_height = 0.0724
        self.meters_to_pixels = self.display_width / self.display_resolution_x
        self.display_distances = [0.0, 0.0724, 0.1448]

        self.headtracker_calibration_mode = True


        self.display_1 = np.array([
            [self.display_width / 2, -self.display_height / 2, self.display_distances[0]],
            [-self.display_width / 2, -self.display_height / 2, self.display_distances[0]],
            [self.display_width / 2, self.display_height / 2, self.display_distances[0]],
            [-self.display_width / 2, self.display_height / 2, self.display_distances[0]],
        ])

        self.display_2 = np.array([
            [self.display_width / 2, -self.display_height / 2, self.display_distances[1]],
            [-self.display_width / 2, -self.display_height / 2, self.display_distances[1]],
            [self.display_width / 2, self.display_height / 2, self.display_distances[1]],
            [-self.display_width / 2, self.display_height / 2, self.display_distances[1]],
        ])

        self.display_3 = np.array([
            [self.display_width / 2, -self.display_height / 2, self.display_distances[2]],
            [-self.display_width / 2, -self.display_height / 2, self.display_distances[2]],
            [self.display_width / 2, self.display_height / 2, self.display_distances[2]],
            [-self.display_width / 2, self.display_height / 2, self.display_distances[2]],  
        ])

        self.displays = [self.display_1, self.display_2, self.display_3]

        self.rotation_correction = Rotation.from_euler('xyz', (0,0,0), degrees=True)
        self.camera_offset = (0, 0.05, 0.0728)
        self.rotation_offset = None
        self.display_offset = 5
        self.mode = "rotation"
        self.hold_threshold = 200

    def create_calibration_frame(self, head_position, hold_value, camera_matrix):
        if self.mode == "rotation":
            #print(head_position)
            marker = np.ones((426, 800, 3), dtype=np.uint8)
            cv2.drawMarker(marker, (400,213), color=(0, 0, 255), markerType=cv2.MARKER_CROSS, 
                markerSize=50, thickness=2)

            percentage = np.clip((hold_value / self.hold_threshold), 0, self.hold_threshold) * 360
            cv2.ellipse(marker, (400,213), (75, 75), 0, 0, percentage, (255, 255, 255), 2)

            offset_array = np.zeros((self.display_offset + 2, 800, 3), dtype=np.uint8).astype(np.uint8)

            target = np.vstack((marker, marker, marker, offset_array)) # stack frames

            target = target[self.display_offset:]

            return target

        if self.mode == "scaling_1" or self.mode == "scaling_2":

            empty = np.zeros((426, 800, 3), dtype=np.uint8)
            frame = np.ones((426, 800, 3), dtype=np.uint8)
            cv2.rectangle(frame, (200, 107), (600, 319), (0, 255, 0), 2)

            if self.mode == "scaling_1":
                display = self.display_2
            else:
                display = self.display_3

            camera_points = display + head_position # find relative display points
            camera_points[:, 1] *= -1 #invert y,z for opencv
            camera_points[:, 2] *= -1

            projected_points = (camera_matrix @ camera_points.T).T # apply projection matrix

            projected_points = (projected_points[:, :2] / projected_points[:, 2:3]).astype(np.float32) # normalize to camera space

            source_points = np.float32([ # initiialize 2D points (image corners)
                [0, 0],             
                [frame.shape[1], 0], 
                [0, frame.shape[0]],
                [frame.shape[1], frame.shape[0]]
            ])

            transform_matrix = cv2.getPerspectiveTransform(source_points, projected_points) # create transformation matrix

            out_size = (frame.shape[1], frame.shape[0])

            warped = cv2.warpPerspective(frame, transform_matrix, out_size, flags=cv2.INTER_LINEAR) # apply it

            offset_array = np.zeros((self.display_offset + 2, 800, 3), dtype=np.uint8).astype(np.uint8)

            if self.mode == "scaling_1":
                target = np.vstack((warped, frame, empty, offset_array)) # stack frames
            else:
                target = np.vstack((warped, empty, frame, offset_array)) # stack frames

            target = target[self.display_offset:]

            return target

    def write_calibration_file():

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

    def solve_camera_rotation(self, head_position):
        target_z = np.sqrt(head_position[0] ** 2 + head_position[1] ** 2 + head_position[2] ** 2 -self.camera_offset[0] ** 2 - self.camera_offset[1] ** 2)

        target = np.array([self.camera_offset[0], -self.camera_offset[1], target_z])

        rotation_offset, _ = Rotation.align_vectors([target], [head_position])

        new_head_position = rotation_offset.apply(head_position)

        #new_head_position = new_head_position + self.camera_offset

        print("new head position:", new_head_position)

        write_display_calibration_file("display_calibration.txt", rotation_offset)

        self.rotation_offset = rotation_offset

        return rotation_offset


    def display_worker(self):
    
        cv2.namedWindow("target_image", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(
            "target_image",
            cv2.WND_PROP_FULLSCREEN,
            cv2.WINDOW_FULLSCREEN
        )

        last_hold_position = np.array([-0.03, 0, 1])
        hold_positions = []
        hold_value = 0
        fail_value = 0
    
        while not self.stop_event.is_set():
            head_position = self.headtracker.acquire_head_position(calibration_mode=True)

            if head_position is None:
                head_position = np.array([-0.03, 0, 1])

            if (np.linalg.norm(head_position - last_hold_position) < 0.07 and not np.allclose(head_position, last_hold_position)):
                hold_positions.append(head_position)
                hold_value += 1

            else:
                fail_value += 1
            
            if fail_value > 5:
                fail_value = 0
                hold_value = 0
                last_hold_position = head_position
                hold_positions = []

            if hold_value == self.hold_threshold:
                mean_position = np.mean(hold_positions, axis=0)
                print("last_hold_position", last_hold_position)
                print("mean position", mean_position)
                self.solve_camera_rotation(mean_position)
                #self.stop_event.set()
                #break
                self.mode = "scaling_1"
                self.headtracker_calibration_mode = False


            fx= (head_position[2]) / self.meters_to_pixels # set focal lenght to normalized head distance
            cx= (self.display_resolution_x) / 2 + head_position[0] / self.meters_to_pixels # set lens offsets to normalized head offsets
            cy= (self.display_resolution_y) / 2 - head_position[1] / self.meters_to_pixels

            camera_matrix = np.array([ # create camera matrix
                [fx, 0, cx],
                [0, fx, cy],
                [0, 0, 1]
            ])

            target_image = self.create_calibration_frame(head_position, hold_value, camera_matrix)
            cv2.imshow("target_image", target_image)

            k = cv2.waitKey(1) & 0xFF
            if k in (13, 10):  # Enter (13 on most systems, 10 on some Linux builds)
                if self.mode == "rotation":
                    self.solve_camera_rotation(head_position)
                    self.mode = "scaling"
            elif k == ord('q'):
                self.stop_event.set()
                break

    def run_multiplane_calibrator(self):

        self.stop_event = threading.Event()

        #headtracker_thread = threading.Thread(target=self.headtracker_worker)

        target_thread = threading.Thread(target=self.display_worker)

        #headtracker_thread.start()
        target_thread.start()

        try:
            while not self.stop_event.is_set():
                time.sleep(0.1)

        except KeyboardInterrupt:
            self.stop_event.set()

        #headtracker_thread.join()
        target_thread.join()
        time.sleep(0.1)
        os._exit(0)

def main():
    cave = MultiplaneCalibrator()
    cave.run_multiplane_calibrator()

if __name__ == "__main__":
    main()