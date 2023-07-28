import torch
from torch import nn
from torch.nn import Parameter
import torch.nn.functional as F
from .Linear import LinearSub
from .qkv import qkv_sub
from ..utils import trunc_normal_
def softmax(x, dim, onnx_trace=False):
    if onnx_trace:
        return F.softmax(x.float(), dim=dim)
    else:
        return F.softmax(x, dim=dim, dtype=torch.float32)

class RelativePosition2D_sub(nn.Module):

    def __init__(self, num_units, max_relative_position):
        super().__init__()

        self.num_units = num_units
        self.max_relative_position = max_relative_position
        # The first element in embeddings_table_v is the vertical embedding for the class
        self.sample_embeddings_table_v = nn.Parameter(torch.randn(max_relative_position * 2 + 2, num_units))
        self.sample_embeddings_table_h = nn.Parameter(torch.randn(max_relative_position * 2 + 2, num_units))

        trunc_normal_(self.sample_embeddings_table_v, std=.02)
        trunc_normal_(self.sample_embeddings_table_h, std=.02)

        self.sample_head_dim = num_units
        

    def set_sample_config(self, sample_head_dim):
        return

    def calc_sampled_param_num(self):
        return self.sample_embeddings_table_h.numel() + self.sample_embeddings_table_v.numel()

    def forward(self, length_q, length_k):
        # remove the first cls token distance computation
        length_q = length_q - 1
        length_k = length_k - 1
        range_vec_q = torch.arange(length_q)
        range_vec_k = torch.arange(length_k)
        # compute the row and column distance
        distance_mat_v = (range_vec_k[None, :] // int(length_q ** 0.5 )  - range_vec_q[:, None] // int(length_q ** 0.5 ))
        distance_mat_h = (range_vec_k[None, :] % int(length_q ** 0.5 ) - range_vec_q[:, None] % int(length_q ** 0.5 ))
        # clip the distance to the range of [-max_relative_position, max_relative_position]
        distance_mat_clipped_v = torch.clamp(distance_mat_v, -self.max_relative_position, self.max_relative_position)
        distance_mat_clipped_h = torch.clamp(distance_mat_h, -self.max_relative_position, self.max_relative_position)

        # translate the distance from [1, 2 * max_relative_position + 1], 0 is for the cls token
        final_mat_v = distance_mat_clipped_v + self.max_relative_position + 1
        final_mat_h = distance_mat_clipped_h + self.max_relative_position + 1
        # pad the 0 which represent the cls token
        final_mat_v = torch.nn.functional.pad(final_mat_v, (1,0,1,0), "constant", 0)
        final_mat_h = torch.nn.functional.pad(final_mat_h, (1,0,1,0), "constant", 0)

        # final_mat_v = torch.LongTensor(final_mat_v).cuda()
        # final_mat_h = torch.LongTensor(final_mat_h).cuda()
        final_mat_v = torch.LongTensor(final_mat_v)
        final_mat_h = torch.LongTensor(final_mat_h)
        # get the embeddings with the corresponding distance
        embeddings = self.sample_embeddings_table_v[final_mat_v] + self.sample_embeddings_table_h[final_mat_h]

        return embeddings

class AttentionSub(nn.Module):
    def __init__(self, embed_dim, num_heads=8, qkv_bias=False, qk_scale=None, attn_drop=0., proj_drop=0., normalization = False, relative_position = False,
                 num_patches = None, max_relative_position=14, scale=False, change_qkv = False):
        super().__init__()
        
        self.sample_in_embed_dim = embed_dim
        self.sample_num_heads = num_heads



        head_dim = embed_dim // num_heads
        self.scale = qk_scale or head_dim ** -0.5

        self.fc_scale = scale
        self.change_qkv = change_qkv
        if change_qkv:
            self.sample_qk_embed_dim = num_heads*64
            self.qkv = qkv_sub(self.sample_in_embed_dim, 3 * self.sample_qk_embed_dim, bias=qkv_bias)
            self.sample_scale = (self.sample_in_embed_dim // self.sample_num_heads) ** -0.5
        else:
            self.sample_qk_embed_dim = self.sample_in_embed_dim
            self.qkv = LinearSub(self.sample_in_embed_dim, 3 * self.sample_qk_embed_dim, bias=qkv_bias)
            self.sample_scale = (self.sample_qk_embed_dim // self.sample_num_heads) ** -0.5

        self.relative_position = relative_position
        self.max_relative_position = max_relative_position
        if self.relative_position:
            self.rel_pos_embed_k = RelativePosition2D_sub(self.sample_qk_embed_dim //self.sample_num_heads, max_relative_position)
            self.rel_pos_embed_v = RelativePosition2D_sub(self.sample_qk_embed_dim //self.sample_num_heads, max_relative_position)

        # self.proj = LinearSuper(super_embed_dim, super_embed_dim)
        self.proj = LinearSub(self.sample_qk_embed_dim, self.sample_in_embed_dim)

        self.attn_drop = nn.Dropout(attn_drop)
        self.proj_drop = nn.Dropout(proj_drop)

    def set_sample_config(self, sample_q_embed_dim=None, sample_num_heads=None, sample_in_embed_dim=None):
        return

    def calc_sampled_param_num(self):
        return 0

    def get_complexity(self, sequence_length):
        total_flops = 0
        total_flops += self.qkv.get_complexity(sequence_length)
        # attn
        total_flops += sequence_length * sequence_length * self.sample_qk_embed_dim
        # x
        total_flops += sequence_length * sequence_length * self.sample_qk_embed_dim
        total_flops += self.proj.get_complexity(sequence_length)
        if self.relative_position:
            total_flops += self.max_relative_position * sequence_length * sequence_length + sequence_length * sequence_length / 2.0
            total_flops += self.max_relative_position * sequence_length * sequence_length + sequence_length * self.sample_qk_embed_dim / 2.0
        return total_flops

    def forward(self, x):
        B, N, C = x.shape
        qkv = self.qkv(x).reshape(B, N, 3, self.sample_num_heads, -1).permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[2]   # make torchscript happy (cannot use tensor as tuple)

        attn = (q @ k.transpose(-2, -1)) * self.sample_scale
        if self.relative_position:
            r_p_k = self.rel_pos_embed_k(N, N)
            attn = attn + (q.permute(2, 0, 1, 3).reshape(N, self.sample_num_heads * B, -1) @ r_p_k.transpose(2, 1)) \
                .transpose(1, 0).reshape(B, self.sample_num_heads, N, N) * self.sample_scale

        attn = attn.softmax(dim=-1)
        attn = self.attn_drop(attn)

        x = (attn @ v).transpose(1,2).reshape(B, N, -1)
        if self.relative_position:
            r_p_v = self.rel_pos_embed_v(N, N)
            attn_1 = attn.permute(2, 0, 1, 3).reshape(N, B * self.sample_num_heads, -1)
            # The size of attention is (B, num_heads, N, N), reshape it to (N, B*num_heads, N) and do batch matmul with
            # the relative position embedding of V (N, N, head_dim) get shape like (N, B*num_heads, head_dim). We reshape it to the
            # same size as x (B, num_heads, N, hidden_dim)
            x = x + (attn_1 @ r_p_v).transpose(1, 0).reshape(B, self.sample_num_heads, N, -1).transpose(2,1).reshape(B, N, -1)

        if self.fc_scale:
            x = x * (self.sample_in_embed_dim / self.sample_qk_embed_dim)
        x = self.proj(x)
        x = self.proj_drop(x)
        return x
