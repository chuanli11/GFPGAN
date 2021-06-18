import os
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--upscale_factor', type=int, default=1)
parser.add_argument('--model_path', type=str, default='experiments/pretrained_models/GFPGANv1.pth')
parser.add_argument('--input_dir', type=str, default='')
parser.add_argument('--output_dir', type=str, default='')
parser.add_argument('--suffix', type=str, default=None, help='Suffix of the restored faces')
parser.add_argument('--only_center_face', action='store_true')
parser.add_argument('--aligned', action='store_true')
parser.add_argument('--paste_back', action='store_true')
parser.add_argument("--gpu_id", dest='gpu_id', default=0, type=int)

args = parser.parse_args()
if args.input_dir.endswith('/'):
    args.input_dir = args.input_dir[:-1]
save_root = args.output_dir
os.makedirs(save_root, exist_ok=True)

# torch.cuda.set_device(opt.gpu_id)
os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"   
os.environ["CUDA_VISIBLE_DEVICES"]=str(args.gpu_id)


import cv2
import glob
import numpy as np
import torch
from facexlib.utils.face_restoration_helper import FaceRestoreHelper
from torchvision.transforms.functional import normalize
from archs.gfpganv1_arch import GFPGANv1
from basicsr.utils import img2tensor, imwrite, tensor2img


def restoration(gfpgan,
                face_helper,
                img_path,
                save_root,
                has_aligned=False,
                only_center_face=True,
                suffix=None,
                paste_back=False):
    # read image
    img_name = os.path.basename(img_path)
    print(f'Processing {img_name} ...')
    basename, _ = os.path.splitext(img_name)
    input_img = cv2.imread(img_path, cv2.IMREAD_COLOR)
    face_helper.clean_all()

    if has_aligned:
        input_img = cv2.resize(input_img, (512, 512))
        face_helper.cropped_faces = [input_img]
    else:
        face_helper.read_image(input_img)
        # get face landmarks for each face
        face_helper.get_face_landmarks_5(only_center_face=only_center_face, pad_blur=False)
        # align and warp each face
        save_crop_path = os.path.join(save_root, 'cropped_faces', img_name)
        face_helper.align_warp_face(save_crop_path)

    # face restoration
    for idx, cropped_face in enumerate(face_helper.cropped_faces):
        # prepare data
        cropped_face_t = img2tensor(cropped_face / 255., bgr2rgb=True, float32=True)
        normalize(cropped_face_t, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=True)
        cropped_face_t = cropped_face_t.unsqueeze(0).to('cuda')

        try:
            with torch.no_grad():
                output = gfpgan(cropped_face_t, return_rgb=False)[0]
                # convert to image
                restored_face = tensor2img(output.squeeze(0), rgb2bgr=True, min_max=(-1, 1))
        except RuntimeError as error:
            print(f'\tFailed inference for GFPGAN: {error}.')
            restored_face = cropped_face

        restored_face = restored_face.astype('uint8')
        face_helper.add_restored_face(restored_face)

        if suffix is not None:
            save_face_name = f'{basename}_{idx:02d}_{suffix}.png'
        else:
            save_face_name = f'{basename}_{idx:02d}.png'
        save_restore_path = os.path.join(save_root, 'restored_faces', save_face_name)
        imwrite(restored_face, save_restore_path)

        # save cmp image
        cmp_img = np.concatenate((cropped_face, restored_face), axis=1)
        imwrite(cmp_img, os.path.join(save_root, 'cmp', f'{basename}_{idx:02d}.png'))

    if not has_aligned and paste_back:
        face_helper.get_inverse_affine(None)
        save_restore_path = os.path.join(save_root, 'restored_imgs', img_name)
        # paste each restored face to the input image
        face_helper.paste_faces_to_input_image(save_restore_path)


if __name__ == '__main__':
    


    # device = torch.device('cuda:' + str(args.gpu_id) if torch.cuda.is_available() else 'cpu')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    # initialize the GFP-GAN
    gfpgan = GFPGANv1(
        out_size=512,
        num_style_feat=512,
        channel_multiplier=1,
        decoder_load_path=None,
        fix_decoder=True,
        # for stylegan decoder
        num_mlp=8,
        input_is_latent=True,
        different_w=True,
        narrow=1,
        sft_half=True)

    gfpgan.to(device)
    checkpoint = torch.load(args.model_path, map_location=lambda storage, loc: storage)
    gfpgan.load_state_dict(checkpoint['params_ema'])
    gfpgan.eval()

    # initialize face helper
    face_helper = FaceRestoreHelper(
        upscale_factor=1, face_size=512, crop_ratio=(1, 1), det_model='retinaface_resnet50', save_ext='png')

    img_list = sorted(glob.glob(os.path.join(args.input_dir, '*')))
    for img_path in img_list:
        restoration(
            gfpgan,
            face_helper,
            img_path,
            save_root,
            has_aligned=args.aligned,
            only_center_face=args.only_center_face,
            suffix=args.suffix,
            paste_back=args.paste_back)

    print('Results are in the <results> folder.')
