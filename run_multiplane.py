from multiplane_renderer import MultiplaneRenderer
import headtracker
from headtracker import HeadTracker
import cv2
import time
import queue
import threading
import os
import subprocess

class Multiplane:
    def __init__(self):
        self.multiplane_renderer = MultiplaneRenderer()
        self.headtracker = HeadTracker()
        self.headtracker.initialize_headtracker()

        self.position_lock = threading.Lock()
        self.last_head_position = self.headtracker.default_head_position
        
        self.render_queue = queue.Queue(maxsize=1)

    def headtracker_worker(self):
        alpha = 0.5  # Smoothing factor

        while not self.stop_event.is_set():
            head_position = self.headtracker.acquire_head_position()

            if head_position is not None:
                with self.position_lock:

                    smoothed_head_position = alpha * head_position + (1 - alpha) * self.last_head_position

                    self.last_head_position = smoothed_head_position

    def renderer_worker(self):

        self.multiplane_renderer.setup_scene()

        while not self.stop_event.is_set():
            if self.last_head_position is not None:
                head_position = self.last_head_position   
                renders = self.multiplane_renderer.render_scene(head_position)

                if self.render_queue.full():
                    try:
                        self.render_queue.get_nowait()
                    except queue.Empty:
                        pass
                self.render_queue.put(renders)

    def compositor_worker(self):
    
        # cv2.namedWindow("combined_image", cv2.WINDOW_NORMAL)
        # cv2.setWindowProperty(
        #     "combined_image",
        #     cv2.WND_PROP_FULLSCREEN,
        #     cv2.WINDOW_FULLSCREEN
        # )
    
        while not self.stop_event.is_set():
            try:
                renders = self.render_queue.get(timeout=0.02)
            except queue.Empty:
                continue
            
            color = renders[0]
            depth = renders[1]
            camera_pose = renders[2]
            camera_matrix = renders[3]

            combined_image = self.multiplane_renderer.composite_frames(color, depth, camera_pose, camera_matrix)
            combined_image = cv2.cvtColor(combined_image, cv2.COLOR_RGB2BGR)
            cv2.imshow("combined_image", combined_image)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.stop_event.set()
                break

    def run_mulitplane(self):

        self.stop_event = threading.Event()

        headtracker_thread = threading.Thread(target=self.headtracker_worker)

        renderer_thread = threading.Thread(target=self.renderer_worker)

        compositor_thread = threading.Thread(target=self.compositor_worker)

        headtracker_thread.start()
        renderer_thread.start()
        compositor_thread.start()

        try:
            while not self.stop_event.is_set():
                time.sleep(0.1)

        except KeyboardInterrupt:
            self.stop_event.set()

        headtracker_thread.join()
        renderer_thread.join()
        compositor_thread.join()
        time.sleep(0.1)
        os._exit(0)

def main():
    try:
        headtracker.read_colmap_file()
    except(FileNotFoundError):
        print("no camera calibration file found, beginning calibration")
        print()
        subprocess.run(["python", "calibrate.py"])

    cave = Multiplane()
    cave.run_mulitplane()

if __name__ == "__main__":
    main()