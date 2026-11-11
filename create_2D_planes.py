import onnxruntime
import cv2
import OpenImageIO as iio
import numpy as np
from PIL import Image
from skimage.filters import threshold_multiotsu

onnx_session = onnxruntime.InferenceSession(
    "ml_models/depth_anything_v2_vits.onnx",
    providers=['CPUExecutionProvider']
)

input_name = onnx_session.get_inputs()[0].name

def preprocess_color(image):
    preprocessed_image = cv2.resize(image, (518, 518))
    preprocessed_image = preprocessed_image.astype(np.float32) / 255.0

    # model normalization
    mean = np.array([0.5, 0.5, 0.5], dtype=np.float32)
    std  = np.array([0.5, 0.5, 0.5], dtype=np.float32)
    preprocessed_image = (preprocessed_image - mean) / std

    preprocessed_image = preprocessed_image.transpose(2, 0, 1)  # HWC → CHW
    preprocessed_image = preprocessed_image[None, ...]          # add batch dim
    
    return preprocessed_image

def infer_depth(image):

    preprocessed_image = preprocess_color(image)

    out = onnx_session.run(None, {input_name: preprocessed_image})
    depth = out[0][0]

    # rescale depth for saving
    depth = (depth - depth.min()) / (depth.max() - depth.min())
    depth = (1 - depth) ** 3
    
    depth = cv2.resize(depth, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)

    return depth

def resize_image(image, depth, target_width, target_height):

    image_width = image.shape[1]
    image_height = image.shape[0]

    target_aspect_ratio = target_width / target_height
    original_aspect_ratio = image_width / image_height

    if target_aspect_ratio <= original_aspect_ratio:
        buffer = int(((image_width / target_aspect_ratio) - image_height) / 2)

        image = cv2.copyMakeBorder(
            image,
            top=buffer,
            bottom=buffer,
            left=0,
            right=0,
            borderType=cv2.BORDER_CONSTANT,
            value=(0, 0, 0)
        )

        depth = cv2.copyMakeBorder(
            depth,
            top=buffer,
            bottom=buffer,
            left=0,
            right=0,
            borderType=cv2.BORDER_CONSTANT,
            value=(0)
        )


    else:
        buffer = int(((image_height * target_aspect_ratio) - image_width) / 2)

        image = cv2.copyMakeBorder(
            image,
            top=0,
            bottom=0,
            left=buffer,
            right=buffer,
            borderType=cv2.BORDER_CONSTANT,
            value=(0, 0, 0)
        )

        depth = cv2.copyMakeBorder(
            depth,
            top=0,
            bottom=0,
            left=buffer,
            right=buffer,
            borderType=cv2.BORDER_CONSTANT,
            value=(0)
        )

    resized_image = cv2.resize(image, (target_width, target_height))
    resized_depth = cv2.resize(depth, (target_width, target_height))

    return resized_image, resized_depth

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

def estimate(image_path, display_width, display_height):
    image_input = iio.ImageInput.open(image_path)
    image = image_input.read_image(iio.UINT8)

    if image.shape[2] == 1:
        print("converting grayscale to rgb")
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)

    image = image[:,:,:3] # remove channels beyond rgb

    depth = infer_depth(image)

    image, depth = resize_image(image, depth, display_width, display_height)

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

    images = [foreground, middleground, background]

    return images


def main():
    display_width = 0.13596
    display_height = 0.0728

    image_path = "images/test_image.jpg"

    estimate(image_path, display_width, display_height, depth_scale=display_height * 3)

if __name__ == "__main__":
    main()

