import onnxruntime
import cv2
import numpy as np
from PIL import Image
from skimage.filters import threshold_multiotsu

onnx_session = onnxruntime.InferenceSession(
    "ml_models/depth_anything_v2_vits.onnx",
    providers=['CPUExecutionProvider']
)

input_name = onnx_session.get_inputs()[0].name

def preprocess_color(image):
    img = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (518, 518))
    img = img.astype(np.float32) / 255.0

    # model normalization
    mean = np.array([0.5, 0.5, 0.5], dtype=np.float32)
    std  = np.array([0.5, 0.5, 0.5], dtype=np.float32)
    img = (img - mean) / std

    img = img.transpose(2, 0, 1)  # HWC → CHW
    img = img[None, ...]          # add batch dim
    
    return img

def infer_depth(image, depth_scale=1.0):

    preprocessed_image = preprocess_color(image)

    out = onnx_session.run(None, {input_name: preprocessed_image})
    depth = out[0][0]

    # rescale depth for saving
    depth = (depth - depth.min()) / (depth.max() - depth.min())
    depth = (1 - depth) ** 3
    depth = depth * depth_scale
    
    depth = cv2.resize(depth, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)

    return depth

def crop_image(image, target_width, target_height):

    image_width = image.shape[1]
    image_height = image.shape[0]

    target_aspect_ratio = target_width / target_height
    original_aspect_ratio = image_width / image_height

    if target_aspect_ratio <= original_aspect_ratio:
        new_width = int(image_height * target_aspect_ratio)
        start_x = int((image_width - new_width) / 2.0)

        cropped_image = image[:,start_x:start_x + new_width]

    else:
        new_height = int(image_width / target_aspect_ratio)
        start_y = int((image_height - new_height) / 2.0)

        cropped_image = image[start_y:start_y+new_height,:]

    resized_image = cv2.resize(cropped_image, (target_width, target_height))

    return resized_image

def create_mask(depth, low_thresh, high_thresh, rolloff_a, rolloff_b):
    mask = np.zeros_like(depth, dtype=np.float32)
    
    lower_ramp = (depth - (low_thresh - 0.5 * rolloff_a)) / (rolloff_a + 1e-6)
    lower_ramp = np.clip(lower_ramp, 0, 1)
    
    upper_ramp = ((high_thresh + 0.5 * rolloff_b) - depth) / (rolloff_b + 1e-6)
    upper_ramp = np.clip(upper_ramp, 0, 1)
    
    if low_thresh == 0:
        mask = upper_ramp
    elif high_thresh == 1:
        mask = lower_ramp
    else:
        mask = np.minimum(lower_ramp, upper_ramp)

    mask = mask ** (1/2.2)
    mask = cv2.blur(mask, (21, 3))
    
    mask = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

    return mask

def estimate(image_path, display_width, display_height, depth_scale=1.0):
    image = cv2.imread(image_path)

    image = crop_image(image, display_width, display_height)

    depth = infer_depth(image, depth_scale=depth_scale)

    return image, depth

def prepare_images(image, depth, gain_luts_2D):

    ot1, ot2 = threshold_multiotsu(depth, classes=3)

    rolloff_a = 0.33
    rolloff_b = 0.33

    foreground_mask = create_mask(depth, 0, ot1, 0.0, rolloff_a)
    foreground = (image * foreground_mask).astype(np.uint8)
    foreground = cv2.LUT(foreground, gain_luts_2D[0])

    middleground_mask = create_mask(depth, ot1, ot2, rolloff_a, rolloff_b)
    middleground = (image * middleground_mask).astype(np.uint8)
    middleground = cv2.LUT(middleground, gain_luts_2D[1])

    background_mask = create_mask(depth, ot2, 1.0, rolloff_b, 0.0)
    background = (image * background_mask).astype(np.uint8)
    background = cv2.LUT(background, gain_luts_2D[2])

    print("mean", np.min(foreground_mask + middleground_mask + background_mask))

    images = [foreground, middleground, background]

    return images


def main():
    display_width = 0.13596
    display_height = 0.0728

    image_path = "images/test_image.jpg"

    estimate(image_path, display_width, display_height, depth_scale=display_height * 3)

if __name__ == "__main__":
    main()

