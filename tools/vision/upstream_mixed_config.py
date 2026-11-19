"""Local override copied next to the upstream SwinV2 Nano config before training."""
_base_ = ["swinv2-nano_cchess16-256.py"]

pipeline = [
    dict(type="LoadImageFromFile"),
    dict(type="CenterCrop", crop_size=(400, 450)),
    dict(type="Resize", scale=(280, 315)),
    dict(type="CChessPackInputs"),
]
train_dataloader = dict(batch_size=8, num_workers=0, persistent_workers=False,
                        dataset=dict(pipeline=pipeline))
val_dataloader = dict(batch_size=8, num_workers=0, persistent_workers=False,
                      dataset=dict(pipeline=pipeline))
train_cfg = dict(by_epoch=True, max_epochs=12, val_interval=1)
param_scheduler = [dict(type="CosineAnnealingLR", eta_min=0.00003, by_epoch=True, end=12)]
optim_wrapper = dict(optimizer=dict(lr=0.0003))
load_from = None
resume = False
randomness = dict(seed=42)
default_hooks = dict(
    _delete_=True,
    logger=dict(type="LoggerHook", interval=20),
    checkpoint=dict(type="CheckpointHook", interval=1, max_keep_ckpts=2,
                    save_best="cchess-full/all_correct_accuracy", rule="greater"),
)
visualizer = dict(_delete_=True, type="Visualizer", vis_backends=[dict(type="LocalVisBackend")])
