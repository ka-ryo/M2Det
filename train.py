from __future__ import print_function
import warnings
from torch.optim import lr_scheduler
from tensorboardX import SummaryWriter
import lib.model_serializer as serializer
from tqdm import tqdm
from torch.utils.data import DataLoader

warnings.filterwarnings('ignore')

from m2det import build_net
from utils.core import *

def parse_args():
    parser = argparse.ArgumentParser(description='M2Det Training')
    parser.add_argument('-c', '--config', default='configs/m2det320_resnet101.py')
    parser.add_argument('-d', '--dataset', default='COCO', choices=['COCO', 'VOC', 'general'])
    parser.add_argument('-rd', '--root_dir')  # this value is enabled if general dataset is selected
    parser.add_argument('-i', '--image_set', default='trainval')  # this value is enabled if general dataset is selected
    parser.add_argument('--resume', '-r', default=None, help='resume net for retraining')
    parser.add_argument('--epoch', '-e', type=int, default=50)
    parser.add_argument('--batch_size', '-b', type=int, default=32)
    parser.add_argument('--num_workers', '-w', type=int, default=10)
    return parser.parse_args()

def get_priors(device, cfg):
    priorbox = PriorBox(anchors(cfg))
    with torch.no_grad():
        priors = priorbox.forward()
        priors = priors.to(device)
    return priors

def get_model(device, cfg):
    net = build_net('train',
                    size=cfg.model.input_size,  # Only 320, 512, 704 and 800 are supported
                    config=cfg.model.m2det_config)
    init_net(net, cfg)
    net.to(device)
    return net

def main():
    print_info('----------------------------------------------------------------------\n'
               '|                       M2Det Training Program                       |\n'
               '----------------------------------------------------------------------', ['yellow', 'bold'])

    args = parse_args()
    cfg = Config.fromfile(args.config)
    config_compile(cfg)

    writer = SummaryWriter()

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    priors = get_priors(device, cfg)
    net = get_model(device, cfg)

    optimizer = set_optimizer(net, cfg)
    criterion = set_criterion(cfg)
    exp_lr_scheduler = lr_scheduler.StepLR(optimizer, step_size=7, gamma=0.1)

    start_epoch = 0
    if args.resume:
        serializer.load_snapshots_to_model(args.resume, net, optimizer, exp_lr_scheduler)
        start_epoch = serializer.load_epoch(args.resume)

    if torch.cuda.device_count() > 1:
        net = torch.nn.DataParallel(net)

    net.train()

    if args.dataset == 'general':
        dataset = get_general_dataset(cfg, args.root_dir, args.image_set)
    else:
        dataset = get_dataloader(cfg, args.dataset, 'train_sets')

    data_loader = DataLoader(dataset,
                                  batch_size=args.batch_size,
                                  shuffle=True,
                                  num_workers=args.num_workers,
                                  collate_fn=detection_collate)

    for epoch in range(start_epoch, args.epoch):
        exp_lr_scheduler.step()

        running_l_loss = 0
        running_c_loss = 0
        print(f"Epoch {epoch} started!")
        for i, data in enumerate(tqdm(data_loader)):
            images, targets = data

            images = images.to(device)
            targets = [anno.to(device) for anno in targets]

            optimizer.zero_grad()
            out = net(images)

            loss_l, loss_c = criterion(out, priors, targets)
            loss = loss_l + loss_c
            running_c_loss += loss_c
            running_l_loss += loss_l

            loss.backward()
            optimizer.step()

        c_loss = running_c_loss / len(data_loader)
        l_loss = running_l_loss / len(data_loader)
        print('epoch: {}, loc_loss: {:.4f}, conf_loss: {:.4f}'.format(epoch, l_loss, c_loss))
        writer.add_scalar('data/loc_loss', l_loss, epoch)
        writer.add_scalar('data/conf_loss', c_loss, epoch)
        if epoch % 5==0:
            serializer.save_snapshots(epoch, net, optimizer, exp_lr_scheduler, f"results/m2det_snapshot_e{epoch}.pt")


if __name__ == '__main__':
    main()