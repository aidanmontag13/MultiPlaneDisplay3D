import headtracker
from headtracker import HeadTracker
import cv2
import time
import queue
import threading
import os
import numpy as np
import glob
import getpass

from scipy.spatial.transform import Rotation

def set_backlight(value):
    for path in glob.glob("/sys/class/backlight/*/brightness"):
        with open(path, "w") as f:
            f.write(str(value))

def write_camera_offset_file(rotation):

    user = getpass.getuser()
    media_root = f"/media/{user}"

    # Loop over all USB drives
    for drive in os.listdir(media_root):
        drive_path = os.path.join(media_root, drive)
        calibration_file = os.path.join(drive_path, "camera_offset_calibration.txt")

    rotation_euler = rotation.as_euler('xyz', degrees=True)
        
    with open(calibration_file, 'w') as f:
        f.write("# Camera list with one line of data per camera:\n")
        f.write("# CAMERA_ID, CAMERA_ROTATION_X, CAMERA_ROTATION_Y, CAMERA_ROTATION_Z\n")
        f.write("# Number of cameras: 1\n")

        f.write(f"0 {rotation_euler[0]} {rotation_euler[1]} {rotation_euler[2]}\n")

class MultiplaneCalibrator:
    def __init__(self):
        self.headtracker = HeadTracker()
        self.headtracker.initialize_headtracker()

        self.position_lock = threading.Lock()
        self.last_head_position = self.headtracker.default_head_position
        
        self.render_queue = queue.Queue(maxsize=1)

        self.display_resolution_x = 800
        self.display_resolution_y = 426

        self.camera_offset = (0, 0.05, 0.0728)
        self.display_offset = 5

        self.hold_threshold = 200

    def create_calibration_frame(self, head_position, hold_value):
        markers = []
        colors = [(0, 0, 255), (184, 0, 0), (0,255,0)]

        for color in colors:
            marker = np.zeros((426, 800, 3), dtype=np.uint8)
            cv2.drawMarker(marker, (400,213), color=color, markerType=cv2.MARKER_CROSS, 
                markerSize=50, thickness=2)

            percentage = np.clip((hold_value / self.hold_threshold), 0, self.hold_threshold) * 360
            cv2.ellipse(marker, (400,213), (75, 75), 0, 0, percentage, (255, 255, 255), 2)

            markers.append(marker)

        offset_array = np.zeros((self.display_offset + 2, 800, 3), dtype=np.uint8).astype(np.uint8)

        target = np.vstack((markers[0], markers[1], markers[2], offset_array)) # stack frames

        text_1 = "Camera Cal 2: Close your left eye, and align yourself with the display"
        text_2 = "so the red green and blue crosses line up, they should appear white "
        text_3 = "Hold there for a few seconds"

        # Draw the text
        cv2.putText(
            target, 
            text_1,
            (20,460),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA
        )

        cv2.putText(
            target, 
            text_2,
            (20,490),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA
        )

        cv2.putText(
            target, 
            text_3,
            (260,800),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA
        )


        target = target[self.display_offset:]

        return target

    def solve_camera_rotation(self, head_position):
        target_z = np.sqrt(head_position[0] ** 2 + head_position[1] ** 2 + head_position[2] ** 2 -self.camera_offset[0] ** 2 - self.camera_offset[1] ** 2)

        target = np.array([self.camera_offset[0], -self.camera_offset[1], target_z])

        rotation_offset, _ = Rotation.align_vectors([target], [head_position])

        new_head_position = rotation_offset.apply(head_position)

        new_head_position = new_head_position + self.camera_offset

        print("new head position:", new_head_position)

        write_camera_offset_file(rotation_offset)


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

        start_time = time.time()
    
        while not self.stop_event.is_set():
            head_position = self.headtracker.acquire_head_position(calibration_mode=True)

            if head_position is None: # move to default position if no head is found
                head_position = np.array([-0.03, 0, 1])

            if (np.linalg.norm(head_position - last_hold_position) < 0.03 and not np.allclose(head_position, last_hold_position)): # see that the head position has moved and is within threshold
                hold_positions.append(head_position)
                hold_value += 1
                fail_value = 0

            if np.linalg.norm(head_position - last_hold_position) > 0.03: # if not within threashold, add to fail value
                fail_value += 1
            
            if fail_value > 5: # if fails for 5 frames in a row, reset position
                fail_value = 0
                hold_value = 0
                last_hold_position = head_position
                hold_positions = []

            if hold_value == self.hold_threshold: # if hold value makes it to threshold, find rotation and exit
                mean_position = np.mean(hold_positions, axis=0)
                self.solve_camera_rotation(mean_position)
                self.stop_event.set()

            if time.time() - start_time < 15:
                hold_value = 0
                fail_value = 0


            target_image = self.create_calibration_frame(head_position, hold_value)
            
            cv2.imshow("target_image", target_image)
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.stop_event.set()

    def run_multiplane_calibrator(self):

        set_backlight(100)

        self.stop_event = threading.Event()

        target_thread = threading.Thread(target=self.display_worker)

        target_thread.start()

        try:
            while not self.stop_event.is_set():
                time.sleep(0.1)

        except KeyboardInterrupt:
            self.stop_event.set()

        target_thread.join()
        time.sleep(0.1)
        os._exit(0)

def main():
    cave = MultiplaneCalibrator()
    cave.run_multiplane_calibrator()

if __name__ == "__main__":
    main()