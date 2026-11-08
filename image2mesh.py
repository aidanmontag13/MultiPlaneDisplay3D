import trimesh
import onnxruntime
import cv2
import numpy as np
from PIL import Image

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

def infer_depth(image, depth_scale):

    preprocessed_image = preprocess_color(image)

    out = onnx_session.run(None, {input_name: preprocessed_image})
    depth = out[0][0]

    # rescale depth for saving
    depth = (depth - depth.min()) / (depth.max() - depth.min())
    depth = 1 - depth
    depth = (depth * depth_scale)

    return depth


def create_mesh(color, depth, fx, fy, cx, cy):

    resized_color = cv2.resize(color, (1024,1024))
    resized_color = cv2.cvtColor(resized_color, cv2.COLOR_BGR2RGB)

    texture = Image.fromarray(resized_color)

    H, W = depth.shape
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    verts = np.stack([(u - cx) * depth / fx, (v - cy) * depth / fy, depth], -1).reshape(-1, 3)

    idx = np.arange(H * W).reshape(H, W)
    tl, tr, bl, br = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    tris = np.concatenate([
        np.stack([tl, bl, tr], -1).reshape(-1, 3),
        np.stack([tr, bl, br], -1).reshape(-1, 3),
    ])

    zf = depth.reshape(-1)
    tz = zf[tris]
    keep = ((tz.max(1) - tz.min(1)) < 0.005)
    tris = tris[keep]

    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    mesh.remove_unreferenced_vertices()
    mesh = mesh.simplify_quadric_decimation(face_count=10000)

    x, y, z = mesh.vertices.T
    px = fx * x / z + cx          # pixel column
    py = fy * y / z + cy          # pixel row
    uv = np.stack([(px + 0.5) / W, 1.0 - (py + 0.5) / H], -1)
    uv = np.clip(uv, 0.0, 1.0)

    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=texture)
    
    return mesh

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

def create_background(image, depth, display_height):
    resized_depth = cv2.resize(depth, (image.shape[1], image.shape[0]))
    
    threshold = (1 + display_height * 2)
    max = 1 + display_height * 3.0

    mask = np.where(resized_depth < threshold, max, 0.0).astype(np.uint8)

    kernel = np.ones((int(21), int(21)), np.uint8)
    mask = cv2.dilate(mask, kernel)

    inpainted = cv2.inpaint(image, mask, 5, cv2.INPAINT_TELEA)
    #inpainted = cv2.GaussianBlur(inpainted, (21, 21), 0)

    cropped_inpainted = crop_image(inpainted, 800, 426)

    return cropped_inpainted

def convert_image(image_path, display_width, display_height, projection_distance=1):
    image = cv2.imread(image_path)

    image_width = image.shape[1]
    image_height = image.shape[0]

    depth = infer_depth(image, depth_scale=display_height * 3)

    depth = depth + projection_distance

    background = create_background(image, depth, display_height)

    fx = (projection_distance / display_width) * 518
    fy = (projection_distance / display_width) * 518 * (image_width / image_height)

    cx = 518 / 2
    cy = 518 / 2

    mesh = create_mesh(image, depth, fx, fy, cx, cy)
    mesh.apply_translation((0, 0, -projection_distance - display_height))
    mesh.export("3Dmodels/test_image/model.obj")

    cv2.imwrite("3Dmodels/test_image/background.jpg", background)

def main():
    display_width = 0.13596
    display_height = 0.0728

    image_path = "images/test_image.jpg"

    convert_image(image_path, display_width, display_height)

if __name__ == "__main__":
    main()

