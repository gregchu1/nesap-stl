"""
Main training script for NERSC PyTorch examples
"""

# System
import os
import argparse
import logging
import socket

# Externals
import yaml
import numpy as np

# Locals
from datasets import get_data_loaders
from trainers import get_trainer
from utils.logging import config_logging
from utils.distributed import init_workers, try_barrier

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser()
    add_arg = parser.add_argument
    add_arg('config', nargs='?', default='configs/hello.yaml',
            help='YAML configuration file')
    add_arg('-d', '--distributed-backend', choices=['mpi', 'nccl', 'gloo'],
            help='Specify the distributed backend to use')
    add_arg('--gpus', type=int, nargs='+',
            help='Choose specific GPUs by device ID')
    add_arg('--ranks-per-node', type=int, default=8,
            help='Specifying number of ranks per node')
    add_arg('--gpus-per-rank', type=int,
            help='Number of gpus per worker rank')
    add_arg('--resume', action='store_true',
            help='Resume training from last checkpoint')
    add_arg('-v', '--verbose', action='store_true',
            help='Enable verbose logging')
    return parser.parse_args()

def load_config(config_file):
    with open(config_file) as f:
        config = yaml.load(f, Loader=yaml.FullLoader)
    return config

def main():
    """Main function"""

    # Initialization
    args = parse_args()
    rank, n_ranks = init_workers(args.distributed_backend)

    # Load configuration
    config = load_config(args.config)

    # Prepare output directory
    output_dir = config.get('output_dir', None)
    if output_dir is not None:
        output_dir = os.path.expandvars(output_dir)
        os.makedirs(output_dir, exist_ok=True)

    # Setup logging
    log_file = (os.path.join(output_dir, 'out_%i.log' % rank)
                if output_dir is not None else None)
    config_logging(verbose=args.verbose, log_file=log_file, append=args.resume)
    logging.info('Initialized rank %i out of %i on host %s',
                 rank, n_ranks, socket.gethostname())
    try_barrier()
    if rank == 0:
        logging.info('Configuration: %s' % config)

    # Load the datasets
    distributed = args.distributed_backend is not None
    train_data_loader, valid_data_loader = get_data_loaders(
        distributed=distributed, **config['data'])

    # Choose GPUs
    if args.gpus is not None:
        gpus = args.gpus
    elif args.gpus_per_rank is not None:
        local_size = min(args.ranks_per_node, n_ranks)
        local_rank = rank % local_size
        gpus = [i * local_size + local_rank for i in range(args.gpus_per_rank)]

        # Causes nccl collective errors in model broadcast
        #gpus = [local_rank*args.gpus_per_rank + i
        #        for i in range(args.gpus_per_rank)]
    else:
        gpus = []
    if len(gpus) > 0:
        logging.info('Using GPUs %s', gpus)

    # Load the trainer
    trainer = get_trainer(name=config['trainer'], distributed=distributed,
                          devices=gpus, rank=rank, output_dir=output_dir)

    # Build the model and optimizer
    trainer.build(config)

    # Resume from checkpoint
    if args.resume:
        trainer.load_checkpoint()

    # Run the training
    summary = trainer.train(train_data_loader=train_data_loader,
                            valid_data_loader=valid_data_loader,
                            **config['train'])

    # Print some conclusions
    try_barrier()
    n_train_samples = len(train_data_loader.sampler)
    logging.info('Finished training')
    train_time = np.mean(summary['train_time'])
    logging.info('Train samples %g time %g s rate %g samples/s',
                 n_train_samples, train_time, n_train_samples / train_time)
    if valid_data_loader is not None:
        n_valid_samples = len(valid_data_loader.sampler)
        valid_time = np.mean(summary['valid_time'])
        logging.info('Valid samples %g time %g s rate %g samples/s',
                     n_valid_samples, valid_time, n_valid_samples / valid_time)

    logging.info('All done!')

if __name__ == '__main__':
    main()
