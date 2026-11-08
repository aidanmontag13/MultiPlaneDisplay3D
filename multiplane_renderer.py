import os
os.environ["PYOPENGL_PLATFORM"] = "egl"
#os.environ["EGL_PLATFORM"] = "surfaceless"

import cv2
import pyrender
from OpenGL import GL

import numpy as np
import trimesh
import time

import create_2D_planes

def create_mask_luts():
    """ Function to create 1D look up table to convert normalized depth buffer into multiplane mask"""
    indicies = np.arange(256, dtype=np.float32) 
    middleground_lut = (np.where(indicies >= 128, (-indicies + 255), indicies) * 2) # middleground lut falls off towards foreground and background
    linear_middleground_lut = ((middleground_lut / 255) ** (1/2.2) * 255).astype(np.uint8)

    foreground_lut = np.where(indicies < 128, 255, middleground_lut) # foreground lut only falls off with background
    linear_foreground_lut = ((foreground_lut / 255) ** (1/2.2) * 255).astype(np.uint8)

    background_lut = np.where(indicies > 128, 255, middleground_lut) # background lut only falls off with foreground
    linear_background_lut = ((background_lut / 255) ** (1/2.2) * 255).astype(np.uint8)

    return [linear_foreground_lut, linear_middleground_lut, linear_background_lut]
    

def create_gain_luts(gains):
    """Function to create 3 channel LUTs to color balance and brighten rendered images"""
    gain_luts = []

    values = np.arange(256, dtype=np.float32)

    for gain in gains:
        print("gain", gain)
        lut = np.empty((256, 1, 3), dtype=np.uint8)
        for channel, channel_value in enumerate(gain):
            print("channel value", channel_value)
            lut[:, 0, channel] = np.clip((values / 255.0) * (channel_value ** (1 / 2.2)) * 255.0, 0, 255)

        gain_luts.append(lut)
    
    return gain_luts

def create_mask(depth, head_position, display_distance, lut):
    """Function to convert depth buffer to mask"""
    #viewer_distance = np.linalg.norm((0, 0, display_distance) + head_position)

    viewer_distance = display_distance + head_position[2] # find viewer distance from display

    mask = depth.copy()
 
    mask[mask == 0] = 100.0 # depth buffer has 0 at infinity, convert to 100
    np.subtract(mask, (viewer_distance - 0.0728), out=mask) # offset viewer distance to 0
    np.divide(mask, 0.1448, out=mask) # normalize to display distances
    np.clip(mask, 0.0, 1.0, out=mask)
    np.multiply(mask, 255.0, out=mask) # convert to 8bit int
    
    mask = mask.astype(np.uint8)
    mask = cv2.LUT(mask, lut) # apply mask
    #erosion_kernel = np.ones((3, 3), np.uint8)
    #mask = cv2.erode(mask, erosion_kernel, iterations=1) # apply erosion to filter aliasing artifacts

    mask = cv2.blur(mask, (15, 1)) # blur depth buffer to reduce aliasing artifacts
        
    return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) # make 3 channel

def warp_image(frame, display_points, camera_position, camera_matrix, pixel_offset):
    """Function to warp image by display corners"""
    
    camera_points = display_points + camera_position # find relative display points
    camera_points[:, 1] *= -1 #invert y,z for opencv
    camera_points[:, 2] *= -1

    projected_points = (camera_matrix @ camera_points.T).T # apply projection matrix

    projected_points = (projected_points[:, :2] / projected_points[:, 2:3]).astype(np.float32) # normalize to camera space

    source_points = np.float32([ # initiialize 2D points (image corners)
        [0, -pixel_offset],             
        [frame.shape[1], -pixel_offset], 
        [0, frame.shape[0] - pixel_offset],
        [frame.shape[1], frame.shape[0] - pixel_offset]
    ])

    transform_matrix = cv2.getPerspectiveTransform(projected_points, source_points) # create transformation matrix

    out_size = (frame.shape[1], frame.shape[0])

    return cv2.warpPerspective(frame, transform_matrix, out_size, flags=cv2.INTER_LINEAR) # apply it

class MultiplaneRenderer:
    def __init__(self):
        self.display_scaler = 0.5
        self.display_offset = int(round(5 * self.display_scaler)) # pixels

        self.display_resolution_x = int(800 * self.display_scaler)
        self.display_resolution_y = int(426 * self.display_scaler)
        self.display_width = 0.13596
        self.display_height = 0.0724
        self.meters_to_pixels = self.display_width / self.display_resolution_x
        self.display_distances = [0.0, 0.0724, 0.1448]

        factor = 6.0
        gains_3D = [(factor * 1.2, factor * 1.0, factor * 0.7), (factor * 0.5, factor * 0.5, factor * 0.5), (factor * 1.5, factor * 1.2, factor * 0.9)]
        self.gain_luts_3D = create_gain_luts(gains_3D)

        gains_2D = [(0.7, 1.0, 1.2), (0.5, 0.5, 0.5), (0.9, 1.2, 1.5)]
        self.gain_luts_2D = create_gain_luts(gains_2D)

        self.mask_luts = create_mask_luts()

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

    def setup_scene(self, file_path):
        """ Setup 3D scene with model and camera"""

        self.display_scaler = 0.5
        self.display_offset = int(round(5 * self.display_scaler)) # pixels
        self.display_resolution_x = int(800 * self.display_scaler)
        self.display_resolution_y = int(426 * self.display_scaler)
        self.meters_to_pixels = self.display_width / self.display_resolution_x
        
        model = trimesh.load(file_path) # use trimesh to import geometry
        #T = trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0])
        #model.apply_transform(T)

        translation_vector = -model.bounding_box.center_mass # move model to center based on verticies
        model.apply_translation(translation_vector)

        model_dimensions = model.bounding_box_oriented.extents # scale model to fit within display dimensions
        model.apply_scale((self.display_width * 1.0) / (np.max(model_dimensions[:2])))

        model.apply_translation((0, 0, -0.0728)) # offset to middle display

        mesh = pyrender.Mesh.from_trimesh(model) # intialize in pyrender

        self.scene = pyrender.Scene(bg_color=[0.0, 0.0, 0.0, 1.0]) # intialize scene
        self.scene.add(mesh)

        camera = pyrender.IntrinsicsCamera( # initialize camera
            fx=1000,
            fy=1000,
            cx=400,
            cy=213,
            znear=0.01,
            zfar=10
        )

        self.camera_node = self.scene.add(camera)

        self.renderer = pyrender.OffscreenRenderer(self.display_resolution_x, self.display_resolution_y) # intialize renderer

        #self.background_image = cv2.imread('./3Dmodels/test_image/background.jpg')
        #self.background_image = cv2.cvtColor(self.background_image, cv2.COLOR_BGR2RGB)
        #self.background_image = (self.background_image * 0.35).astype(np.uint8)

    def render_scene(self, head_position):
        """function to render scene based on head position"""
        start_time = time.time()
        camera_pose = np.array([ # create transformation matrix
            [1.0, 0.0, 0.0, head_position[0]],
            [0.0, 1.0, 0.0, head_position[1]],
            [0.0, 0.0, 1.0, head_position[2]],
            [0.0, 0.0, 0.0, 1.0],
        ])
    
        self.camera_node.matrix = camera_pose

        fx= (head_position[2]) / self.meters_to_pixels # set focal lenght to normalized head distance
        cx= (self.display_resolution_x) / 2 + head_position[0] / self.meters_to_pixels # set lens offsets to normalized head offsets
        cy= (self.display_resolution_y) / 2 - head_position[1] / self.meters_to_pixels

        camera_matrix = np.array([ # create camera matrix
            [fx, 0, cx],
            [0, fx, cy],
            [0, 0, 1]
        ])
        
        self.camera_node.camera._fx = fx # apply camera matrix
        self.camera_node.camera._fy = fx
        self.camera_node.camera._cx = cx
        self.camera_node.camera._cy = cy

        render_start_time = time.time()
        color, depth = self.renderer.render(self.scene, flags=pyrender.RenderFlags.FLAT) # render scene and depth buffer

        #alpha = cv2.cvtColor(np.where(depth == 0, 1, 0).astype(np.uint8), cv2.COLOR_GRAY2RGB)

        #background = cv2.multiply(self.background_image, alpha)
        #color = color[:, :, :3] + background

        end_time = time.time()
        print("rendering time took", end_time - start_time)

        return color, depth, head_position, camera_matrix

    def composite_frames(self, color, depth, head_position, camera_matrix):
        """Function to convert rendered image into multiplane output"""
        start_time = time.time()
         # intiialize erosion kernel to filter aliasing artifacts
        frames = []
        for i, (display_points, display_distance, mask_lut, gain_lut) in enumerate(zip(self.displays, self.display_distances, self.mask_luts, self.gain_luts_3D)):
            masked_color = cv2.LUT(color, gain_lut) # apply gain lut
            mask = create_mask(depth, head_position, display_distance, mask_lut) # create mask
            masked_color = cv2.multiply(masked_color, mask, scale=1.0/255.0) # apply mask

            #blurred_masked_color = cv2.blur(masked_color, (21,1))

            #masked_color = cv2.multiply(masked_color, mask, scale=1.0/255.0) + cv2.multiply(blurred_masked_color, (255 - mask), scale=1.0/255.0)

            #masked_color = cv2.erode(masked_color, erosion_kernel, iterations=1) # apply erosion to filter aliasing artifacts
            projected_frame = warp_image(masked_color, display_points, head_position, camera_matrix, self.display_offset) # warp image

            frames.append(projected_frame)


        stacked_frames = np.vstack((frames[0], frames[1], frames[2])) # stack frames

        end_time = time.time()
        print("compositing time took", end_time - start_time)

        return stacked_frames

    def setup_2D_images(self, image_path):

        self.display_scaler = 1.0
        self.display_offset = int(round(5 * self.display_scaler)) # pixels

        self.display_resolution_x = int(800 * self.display_scaler)
        self.display_resolution_y = int(426 * self.display_scaler)
        self.meters_to_pixels = self.display_width / self.display_resolution_x
        

        image, depth = create_2D_planes.estimate(
            image_path, 
            self.display_resolution_x, 
            self.display_resolution_y, 
        )

        frames = create_2D_planes.prepare_images(image, depth, self.gain_luts_2D)
        
        black_frame = np.zeros_like(image)
        flat_frame = np.vstack((image, black_frame, black_frame))
        flat_frame = cv2.LUT(flat_frame, self.gain_luts_2D[0])

        return frames, flat_frame

    def composite_2D_frames(self, frames, head_position):
            """Function to convert rendered image into multiplane output"""
            start_time = time.time()

            projected_frames = []

            fx= (head_position[2]) / self.meters_to_pixels # set focal lenght to normalized head distance
            cx= (self.display_resolution_x) / 2 + head_position[0] / self.meters_to_pixels # set lens offsets to normalized head offsets
            cy= (self.display_resolution_y) / 2 - head_position[1] / self.meters_to_pixels

            camera_matrix = np.array([ # create camera matrix
                [fx, 0, cx],
                [0, fx, cy],
                [0, 0, 1]
            ])

            for i, (frame, display_points) in enumerate(zip(frames, self.displays)):
                offset_display_points = display_points# - (0,0, self.display_distances[1] / 2)
    
                projected_frame = warp_image(frame, offset_display_points, head_position, camera_matrix, self.display_offset) # warp image
    
                projected_frames.append(projected_frame)
    
            stacked_frames = np.vstack((projected_frames[0], projected_frames[1], projected_frames[2])) # stack frames
    
            end_time = time.time()
            #print("compositing time took", end_time - start_time)
    
            return stacked_frames
