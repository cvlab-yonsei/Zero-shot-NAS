import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import torch
from torch import nn
import numpy as np
import global_utils, argparse, ModelLoader, time

def kaiming_normal_fanin_init(m):
    if isinstance(m, nn.Conv2d) or isinstance(m, nn.Linear):
        nn.init.kaiming_normal_(m.weight, mode='fan_in', nonlinearity='relu')
        if hasattr(m, 'bias') and m.bias is not None:
            nn.init.zeros_(m.bias)
    elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
        if m.affine:
            nn.init.ones_(m.weight)
            nn.init.zeros_(m.bias)

def kaiming_normal_fanout_init(m):
    if isinstance(m, nn.Conv2d) or isinstance(m, nn.Linear):
        nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
        if hasattr(m, 'bias') and m.bias is not None:
            nn.init.zeros_(m.bias)
    elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
        if m.affine:
            nn.init.ones_(m.weight)
            nn.init.zeros_(m.bias)

def init_model(model, method='kaiming_norm_fanin'):
    if method == 'kaiming_norm_fanin':
        model.apply(kaiming_normal_fanin_init)
    elif method == 'kaiming_norm_fanout':
        model.apply(kaiming_normal_fanout_init)
    # elif method == 'kaiming_uni_fanin':
    #     model.apply(kaiming_uniform_fanin_init)
    # elif method == 'kaiming_uni_fanout':
    #     model.apply(kaiming_uniform_fanout_init)
    # elif method == 'xavier_norm':
    #     model.apply(xavier_normal)
    # elif method == 'xavier_uni':
    #     model.apply(xavier_uniform)
    # elif method == 'plain_norm':
    #     model.apply(plain_normal)
    # else:
    #     raise NotImplementedError
    return model


def compute_nas_score(gpu, model, resolution, batch_size, fp16=False):
    model.train()
    model.cuda()
    info = {}
    nas_score_list = []
    if gpu is not None:
        device = torch.device('cuda:{}'.format(gpu))
    else:
        device = torch.device('cpu')

    if fp16:
        dtype = torch.half
    else:
        dtype = torch.float32

    init_model(model, 'kaiming_norm_fanin')

    input_ = torch.randn(size=[batch_size, 3, resolution, resolution], device=device, dtype=dtype)
    
    layer_features, stage_features = model.extract_layer_and_stage_features(input_)

    ################ fwrd pca score ################
    # feat = features[-1].detach().clone()
    # b,c,h,w = feat.size()
    # feat = feat.permute(0,2,3,1).contiguous().view(b*h*w,c)
    # m = feat.mean(dim=0, keepdim=True)
    # feat = feat - m
    # sigma = torch.mm(feat.transpose(1,0),feat) / (feat.size(0))
    # u, s, v = torch.svd(sigma, compute_uv=False)
    # prob_s = s / s.sum()
    # score = (-prob_s)*torch.log(prob_s+1e-8)
    # score = score.sum()
    # fwrd_pca_score = score.item()

    scores = []
    for i in range(len(stage_features)):
        feat = stage_features[i].detach().clone()
        b,c,h,w = feat.size()
        feat = feat.permute(0,2,3,1).contiguous().view(b*h*w,c)
        m = feat.mean(dim=0, keepdim=True)
        feat = feat - m
        sigma = torch.mm(feat.transpose(1,0),feat) / (feat.size(0))
        u, s, v = torch.svd(sigma, compute_uv=False)
        prob_s = s / s.sum()
        score = (-prob_s)*torch.log(prob_s+1e-8)
        score = score.sum().item()
        scores.append(score)
    fwrd_pca_score = np.mean(scores)
    #################################################

    ################ fwrd norm score ################
    # cell_features = features
    # scores = []
    # for i in range(1, len(cell_features)):
    #     f_out = cell_features[i]
    #     f_in = cell_features[i-1]

    #     if (f_out.size() == f_in.size()) and (torch.all(f_in == f_out)):
    #         scores.append(-np.inf)
    #     else:
    #         if f_out.size(2) != f_in.size(2) or f_out.size(3) != f_in.size(3):
    #             bo,co,ho,wo = f_out.size()
    #             bi,ci,hi,wi = f_in.size()
    #             stride = int(hi/ho)
    #             pixel_unshuffle = nn.PixelUnshuffle(stride)
    #             f_in = pixel_unshuffle(f_in)
    #         s = f_out.norm(p=2, dim=(1)).mean() / (f_in.norm(p=2, dim=(1)).mean()+1e-6)
    #         scores.append(-s.item() - 1/(s.item()+1e-6) + 2)
    # fwrd_norm_score = np.mean(scores)
    fwrd_norm_score = 0
    #################################################

    ################ spec norm score ##############
    cell_features = layer_features
    scores = []
    for i in reversed(range(1, len(cell_features))):
        f_out = cell_features[i]
        f_in = cell_features[i-1]
        if f_out.grad is not None:
            f_out.grad.zero_()
            f_in.grad.zero_()
        
        g_out = torch.ones_like(f_out) * 0.5
        g_out = (torch.bernoulli(g_out) - 0.5) * 2
        g_in = torch.autograd.grad(outputs=f_out, inputs=f_in, grad_outputs=g_out, retain_graph=False)[0]
        if g_out.size()==g_in.size() and torch.all(g_in == g_out):
            scores.append(-np.inf)
        else:
            if g_out.size(2) != g_in.size(2) or g_out.size(3) != g_in.size(3):
                bo,co,ho,wo = g_out.size()
                bi,ci,hi,wi = g_in.size()
                stride = int(hi/ho)
                pixel_unshuffle = nn.PixelUnshuffle(stride)
                g_in = pixel_unshuffle(g_in)
            bo,co,ho,wo = g_out.size()
            bi,ci,hi,wi = g_in.size()
            ### straight-forward way
            # g_out = g_out.permute(0,2,3,1).contiguous().view(bo*ho*wo,1,co)
            # g_in = g_in.permute(0,2,3,1).contiguous().view(bi*hi*wi,ci,1)
            # mat = torch.bmm(g_in,g_out).mean(dim=0)
            ### efficient way # print(torch.allclose(mat, mat2, atol=1e-6))
            g_out = g_out.permute(0,2,3,1).contiguous().view(bo*ho*wo,co)
            g_in = g_in.permute(0,2,3,1).contiguous().view(bi*hi*wi,ci)
            mat = torch.mm(g_in.transpose(1,0),g_out) / (bo*ho*wo)
            u, s, v = torch.svd(mat, compute_uv=False)
            scores.append(-s.max().item() - 1/(s.max().item()+1e-6)+2)
    bkwd_norm_score = np.mean(scores)
    #################################################

    info['expressivity'] = float(fwrd_pca_score)
    info['stability'] = float(fwrd_norm_score)
    info['trainability'] = float(bkwd_norm_score)
    info['capacity'] = float(model.get_model_size())
    return info


def parse_cmd_options(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch_size', type=int, default=16, help='number of instances in one mini-batch.')
    parser.add_argument('--input_image_size', type=int, default=None,
                        help='resolution of input image, usually 32 for CIFAR and 224 for ImageNet.')
    parser.add_argument('--repeat_times', type=int, default=32)
    parser.add_argument('--gpu', type=int, default=None)
    parser.add_argument('--mixup_gamma', type=float, default=1e-2)
    module_opt, _ = parser.parse_known_args(argv)
    return module_opt

if __name__ == "__main__":
    opt = global_utils.parse_cmd_options(sys.argv)
    args = parse_cmd_options(sys.argv)
    the_model = ModelLoader.get_model(opt, sys.argv)
    if args.gpu is not None:
        the_model = the_model.cuda(args.gpu)


    start_timer = time.time()
    info = compute_nas_score(gpu=args.gpu, model=the_model,
                             resolution=args.input_image_size, batch_size=args.batch_size, fp16=False)
    time_cost = (time.time() - start_timer)
    expressivity = info['expressivity']
    stability = info['stability']
    trainability = info['trainability']
    capacity = info['capacity']
    print(f'Expressivity={expressivity:.4g}, Stability={stability:.4g}, Trainability={trainability:.4g}, Capacity={capacity:.4g}, time cost={time_cost:.4g} second(s)')