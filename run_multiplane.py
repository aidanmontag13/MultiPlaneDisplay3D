from multiplane_renderer import MultiplaneRenderer
import headtracker
from headtracker import HeadTracker
import cv2
import time
import queue
import threading
import os
import subprocess
import random
import glob
import numpy as np
import getpass

def downsample_object_textures():

    user = getpass.getuser()
    media_root = f"/media/{user}"

    texture_paths = []

    # Loop over all USB drives
    for drive in os.listdir(media_root):
        drive_path = os.path.join(media_root, drive)
        models_folder = os.path.join(drive_path, "models")

    if os.path.isdir(models_folder):
        print(f"Found models folder on USB: {models_folder}")

        for dirpath, dirnames, filenames in os.walk(models_folder):
            for ext in ("*.jpg", "*.jpeg", "*.png", "*.tif", "*.tiff"):
                texture_paths.extend(glob.glob(os.path.join(dirpath, ext)))
    else:
        print(f"No models folder found on USB: {drive_path}")

    print("texture paths", texture_paths)

    for texture_path in texture_paths:
        texture = cv2.imread(texture_path)

        texture_width = texture.shape[1]
        texture_height = texture.shape[0]

        if texture_width > 1024 or texture_height > 1024:
            print("downscaling", texture_path)

            new_width = int(1024)
            new_height = int(1024 * (texture_height / texture_width))
            texture = cv2.resize(texture, (new_width, new_height), cv2.INTER_AREA)

            cv2.imwrite(texture_path, texture)

def find_usb_files():
    user = getpass.getuser()
    media_root = f"/media/{user}"

    image_paths = []
    model_paths = []

    # Loop over all USB drives
    for drive in os.listdir(media_root):
        drive_path = os.path.join(media_root, drive)
        images_folder = os.path.join(drive_path, "images")
        models_folder = os.path.join(drive_path, "models")

        if os.path.isdir(images_folder):
            print(f"Found images folder on USB: {images_folder}")

            # Add all image files (jpg, png, tiff)
            for ext in ("*.jpg", "*.jpeg", "*.png", "*.tif", "*.tiff", "*.heic", "*.heif", "*.webp"):
                image_paths.extend(glob.glob(os.path.join(images_folder, ext)))
        else:
            print(f"No images folder found on USB: {drive_path}")
            continue

        if os.path.isdir(models_folder):
            print(f"Found models folder on USB: {models_folder}")

            for dirpath, dirnames, filenames in os.walk(models_folder):
                # Add all model files (obj, glb, fbx)
                for ext in ("*.obj", "*.glb", ".gltf"):
                    model_paths.extend(glob.glob(os.path.join(dirpath, ext)))
        else:
            print(f"No models folder found on USB: {drive_path}")

    print("image paths", image_paths)
    print("model paths", model_paths)

    return image_paths, model_paths

def set_backlight(value):
    for path in glob.glob("/sys/class/backlight/*/brightness"):
        with open(path, "w") as f:
            f.write(str(value))

class Multiplane:
    def __init__(self):
        self.multiplane_renderer = MultiplaneRenderer()
        self.headtracker = HeadTracker()
        self.headtracker.initialize_headtracker()

        self.position_lock = threading.Lock()
        self.head_position = self.headtracker.default_head_position
        self.last_head_position = self.headtracker.default_head_position
        
        self.render_queue = queue.Queue(maxsize=1)
        self.composite_queue = queue.Queue(maxsize=1)

        self.render_duration = 30.0
        self.file_start_time = time.time()

        self.flat_frame = None

        self.idle_mode = False

    def headtracker_worker(self):

        idle_time = time.time()

        while not self.quit_event.is_set():

            if self.idle_mode == True:
                time.sleep(1.0)
    
            start_time = time.time()
            head_position = self.headtracker.acquire_head_position()
            end_time = time.time()
            #print("headtracking took", end_time - start_time, "seconds")

            if head_position is not None:
                if self.idle_mode == True:
                    self.idle_mode = False
                    set_backlight(100)

                with self.position_lock:
                    self.head_position = head_position
                    idle_time = time.time()
        
            if time.time() - idle_time > 5 and self.idle_mode is False:
                set_backlight(0)
                self.idle_mode = True

    def renderer_worker(self, file_path):

        alpha = 0.2  # Smoothing factor

        self.multiplane_renderer.setup_scene(file_path)

        while not self.renderer_stop_event.is_set():
            if self.idle_mode == True:
                time.sleep(1.0)
                continue

            if self.head_position is not None:
                head_position = self.head_position   

                smoothed_head_position = alpha * head_position + (1 - alpha) * self.last_head_position
                self.last_head_position = smoothed_head_position

                renders = self.multiplane_renderer.render_scene(smoothed_head_position)

                if self.render_queue.full():
                    try:
                        self.render_queue.get_nowait()
                    except queue.Empty:
                        pass
                self.render_queue.put(renders)

    def compositor_worker(self):
        self.flat_frame = None
    
        while not self.renderer_stop_event.is_set():
            if self.idle_mode == True:
                time.sleep(1.0)
                continue

            try:
                renders = self.render_queue.get(timeout=0.02)
            except queue.Empty:
                continue
            
            color = renders[0]
            depth = renders[1]
            camera_pose = renders[2]
            camera_matrix = renders[3]

            combined_image = self.multiplane_renderer.composite_frames(color, depth, camera_pose, camera_matrix)
            
            if self.composite_queue.full():
                try:
                    self.composite_queue.get_nowait()
                except queue.Empty:
                    pass
            self.composite_queue.put(combined_image)

    def renderer_2D_worker(self, image_path):
        
        alpha = 0.02  # Smoothing factor
        
        frames, self.flat_frame = self.multiplane_renderer.setup_2D_images(image_path)

        while not self.renderer_stop_event.is_set():

            if self.idle_mode == True:
                time.sleep(1.0)
                continue

            if self.head_position is not None:
                head_position = self.head_position   

                smoothed_head_position = alpha * head_position + (1 - alpha) * self.last_head_position
                self.last_head_position = smoothed_head_position

                combined_image = self.multiplane_renderer.composite_2D_frames(frames, smoothed_head_position)

                if self.composite_queue.full():
                    try:
                        self.composite_queue.get_nowait()
                    except queue.Empty:
                        pass
                self.composite_queue.put(combined_image)

    def display_worker(self):
    
        cv2.namedWindow("combined_image", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(
            "combined_image",
            cv2.WND_PROP_FULLSCREEN,
            cv2.WINDOW_FULLSCREEN
        )

        timeout_frames = 0
        start_offset = 3.0
        fade_time = 1.0
        flat_time = 3.0
        fadein_time = fade_time + flat_time

        black_frame = np.zeros((100,100,3)).astype(np.uint8)
        shutter = False

    
        while not self.quit_event.is_set():

            if self.idle_mode == True:
                if shutter == False:
                    cv2.imshow("combined_image", black_frame)
                    shutter = True

                cv2.waitKey(1)
                time.sleep(1.0)
                continue
            
            else:
                if shutter == True:
                    shutter = False

            try:
                stacked_image = self.composite_queue.get(timeout=0.02)
            except queue.Empty:
                continue

            render_time = time.time() - self.file_start_time

            if self.flat_frame is not None: # for 2D images, have flat to 2.5D transition

                if (render_time - start_offset) < fade_time:
                    alpha = np.clip((render_time - start_offset) / fade_time, 0.0, 1.0)
                    stacked_image = (self.flat_frame * alpha).astype(np.uint8)

                if (render_time - start_offset) < fadein_time and render_time - start_offset > fade_time:
                    alpha = flat_time * (fadein_time - (render_time - start_offset)) / fadein_time
                    alpha = np.clip((alpha), 0.0, 1.0)

                    stacked_image = cv2.addWeighted(stacked_image, (1.0-alpha), self.flat_frame, alpha, 0.0)
            
            else:
                if (render_time - start_offset) < fade_time:
                    alpha = np.clip((render_time - start_offset) / fade_time, 0.0, 1.0)
                    stacked_image = (stacked_image * alpha).astype(np.uint8)


            if render_time > self.render_duration - fade_time: # always fade out for 2D and 3D
                alpha = np.clip(((self.render_duration - render_time) / fade_time), 0.0, 1.0)
                stacked_image = (stacked_image * alpha).astype(np.uint8)

            stacked_image = cv2.cvtColor(stacked_image, cv2.COLOR_RGB2BGR)
            
            cv2.imshow("combined_image", stacked_image)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.renderer_stop_event.set()
                break

    def run_mulitplane(self):

        set_backlight(100)

        multiplane_start_time = time.time()

        self.quit_event = threading.Event()
        self.renderer_stop_event = threading.Event()

        headtracker_thread = threading.Thread(target=self.headtracker_worker)
        headtracker_thread.start()

        display_thread = threading.Thread(target=self.display_worker)
        display_thread.start()

        downsample_object_textures()

        image_paths, model_paths = find_usb_files()
        file_paths = image_paths + model_paths
        random.shuffle(file_paths)
        file_index = 0

        while time.time() - multiplane_start_time < 3600 and not self.quit_event.is_set(): # run for 1 hour

            if self.idle_mode == True:
                time.sleep(0.1)
                continue

            self.renderer_stop_event.clear()   

            file_path = file_paths[file_index]
            print("displaying file:", file_path)

            file_index += 1

            if file_index >= len(file_paths):
                random.shuffle(file_paths)
                file_index = 0


            self.file_start_time = time.time()

            if file_path.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".heif", ".webp")):
                renderer_thread = threading.Thread(target=self.renderer_2D_worker, args=(file_path,))
                renderer_thread.start()
                compositor_thread = None

            if file_path.lower().endswith((".obj", ".glb", ".gltf")):
                renderer_thread = threading.Thread(target=self.renderer_worker, args=(file_path,))
                renderer_thread.start()

                compositor_thread = threading.Thread(target=self.compositor_worker)
                compositor_thread.start()

            try:
                while not self.renderer_stop_event.is_set() and time.time() - self.file_start_time < self.render_duration: # run each image for 30 seconds
                    time.sleep(0.1)

            except KeyboardInterrupt:
                self.quit_event.set()
                self.renderer_stop_event.set()

            self.renderer_stop_event.set()

            renderer_thread.join()

            if compositor_thread is not None:
                compositor_thread.join()

            time.sleep(0.1)
        
        self.renderer_stop_event.set()
        self.quit_event.set()

        headtracker_thread.join()
        display_thread.join()
        cv2.destroyAllWindows()
        time.sleep(0.1)
        os._exit(0)

def main():
    subprocess.Popen(["unclutter", "-idle", "0", "-root"])
    try:
        headtracker.read_camera_intrinsic_file()
    except(FileNotFoundError, UnboundLocalError):
        print("no camera calibration file found, beginning calibration")
        print()
        subprocess.run(["python", "calibrate_camera_intrinsics.py"])

    try:
        headtracker.read_camera_offset_file()
    except(FileNotFoundError, UnboundLocalError):
        print("no camera calibration file found, beginning calibration")
        print()
        subprocess.run(["python", "calibrate_camera_offset.py"])

    cave = Multiplane()
    cave.run_mulitplane()

if __name__ == "__main__":
    main()