#!/bin/bash
cd "$(dirname "$0")"
set -e

# echo -e "Enter the metric"
# read metric
# echo -e "Enter the gpu"
# read gpu
# echo -e "Enter the seed"
# read seed
# echo "Run this script with metric=$metric, search gpu=$gpu, seed=$seed"

if [ "$#" -lt 6 ] || [ "$#" -gt 6 ]; then
    echo "$# is Illegal number of parameters."
    echo "Usage: *.sh metric pop_size evo_iter seed num_workers init_method"
	exit 1
fi

metric=$1
population_size=$2
evolution_max_iter=$3
seed=$4
num_workers=$5
init=$6
echo "Run this script with metric=$metric, population_size=$population_size, evolution_max_iter=$evolution_max_iter, seed=$seed, num_workers=$num_workers, init=$init"

cd ../

save_dir=./save_dir/${metric}_flops600M-searchbs64-pop${population_size}-iter${evolution_max_iter}-topkmut-${seed}
mkdir -p ${save_dir}
evolution_max_iter=$(printf "%.0f" $evolution_max_iter)

resolution=224
budget_flops=600e6
max_layers=14
# population_size=1024
epochs=480
# evolution_max_iter=100000

echo "SuperConvK3BNRELU(3,8,2,1)SuperResIDWE6K3(8,32,2,8,1)SuperResIDWE6K3(32,48,2,32,1)\
SuperResIDWE6K3(48,96,2,48,1)SuperResIDWE6K3(96,128,2,96,1)\
SuperConvK1BNRELU(128,2048,1,1)" > ${save_dir}/init_plainnet.txt

# python evolution_search_etf_topkmut.py --gpu ${gpu} \
#   --zero_shot_score ${metric} \
#   --search_space SearchSpace/search_space_IDW_fixfc.py \
#   --budget_flops ${budget_flops} \
#   --max_layers ${max_layers} \
#   --batch_size 64 \
#   --input_image_size ${resolution} \
#   --plainnet_struct_txt ${save_dir}/init_plainnet.txt \
#   --num_classes 1000 \
#   --evolution_max_iter ${evolution_max_iter} \
#   --population_size ${population_size} \
#   --save_dir ${save_dir} \
#   --dataset imagenet-1k \
#   --num_worker 0 \
#   --rand_input True \
#   --search_no_res False \
#   --seed ${seed} \
#   --datapath /dataset/ILSVRC2012/

# python analyze_model.py \
#   --input_image_size 224 \
#   --num_classes 1000 \
#   --arch Masternet.py:MasterNet \
#   --plainnet_struct_txt ${save_dir}/best_structure.txt

horovodrun -np 8 python ts_train_image_classification.py --dataset imagenet --num_classes 1000 \
  --dist_mode single --workers_per_gpu ${num_workers} \
  --input_image_size ${resolution} --epochs ${epochs} --warmup 5 \
  --optimizer sgd --bn_momentum 0.01 --wd 4e-5 --nesterov --weight_init ${init} \
  --label_smoothing --random_erase --mixup --auto_augment \
  --lr_per_256 0.1 --target_lr_per_256 0.0 --lr_mode cosine \
  --arch Masternet.py:MasterNet \
  --plainnet_struct_txt ${save_dir}/best_structure.txt \
  --teacher_arch geffnet_tf_efficientnet_b3_ns \
  --teacher_pretrained \
  --teacher_input_image_size 320 \
  --teacher_feature_weight 1.0 \
  --teacher_logit_weight 1.0 \
  --ts_proj_no_relu \
  --ts_proj_no_bn \
  --use_se \
  --target_downsample_ratio 16 \
  --batch_size_per_gpu 64 --save_dir ${save_dir}/ts_training_epochs${epochs}_init-${init} \
  --world-size 8 \
  --dist_mode horovod\
