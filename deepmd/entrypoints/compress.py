"""Compress a model, which including tabulating the embedding-net."""

import json
import logging
from typing import Optional

from deepmd.env import tf
from deepmd.common import j_loader, get_tensor_by_name, GLOBAL_TF_FLOAT_PRECISION
from deepmd.utils.argcheck import normalize
from deepmd.utils.compat import updata_deepmd_input
from deepmd.utils.errors import GraphTooLargeError, GraphWithoutTensorError

from .freeze import freeze
from .train import train
from .transfer import transfer

__all__ = ["compress"]

log = logging.getLogger(__name__)


def compress(
    *,
    input: str,
    output: str,
    extrapolate: int,
    step: float,
    frequency: str,
    checkpoint_folder: str,
    mpi_log: str,
    log_path: Optional[str],
    log_level: int,
    **kwargs
):
    """Compress model.

    The table is composed of fifth-order polynomial coefficients and is assembled from
    two sub-tables. The first table takes the step parameter as the domain's uniform step size,
    while the second table takes 10 * step as it's uniform step size. The range of the
    first table is automatically detected by the code, while the second table ranges
    from the first table's upper boundary(upper) to the extrapolate(parameter) * upper.

    Parameters
    ----------
    input : str
        frozen model file to compress
    output : str
        compressed model filename
    extrapolate : int
        scale of model extrapolation
    step : float
        uniform step size of the tabulation's first table
    frequency : str
        frequency of tabulation overflow check
    checkpoint_folder : str
        trining checkpoint folder for freezing
    mpi_log : str
        mpi logging mode for training
    log_path : Optional[str]
        if speccified log will be written to this file
    log_level : int
        logging level
    """
    try:
        t_jdata = get_tensor_by_name(input, 'train_attr/training_script')
        t_min_nbor_dist = get_tensor_by_name(input, 'train_attr/min_nbor_dist')
    except GraphWithoutTensorError as e:
        raise RuntimeError(
            "The input frozen model: %s has no training script or min_nbor_dist information,"
            "which is not supported by the model compression program."
            "Please consider using the dp convert-from interface to upgrade the model" % input
        ) from e
    tf.constant(t_min_nbor_dist,
        name = 'train_attr/min_nbor_dist',
        dtype = GLOBAL_TF_FLOAT_PRECISION)
    jdata = json.loads(t_jdata)
    jdata["model"]["compress"] = {}
    jdata["model"]["compress"]["type"] = 'se_e2_a'
    jdata["model"]["compress"]["compress"] = True
    jdata["model"]["compress"]["model_file"] = input
    jdata["model"]["compress"]["min_nbor_dist"] = t_min_nbor_dist
    jdata["model"]["compress"]["table_config"] = [
        extrapolate,
        step,
        10 * step,
        int(frequency),
    ]
    jdata = normalize(jdata)

    # check the descriptor info of the input file
    assert (
        jdata["model"]["descriptor"]["type"] == "se_a" or jdata["model"]["descriptor"]["type"] == "se_e2_a"
    ), "Model compression error: descriptor type must be se_a or se_e2_a!"
    assert (
        jdata["model"]["descriptor"]["resnet_dt"] is False
    ), "Model compression error: descriptor resnet_dt must be false!"

    # stage 1: training or refining the model with tabulation
    log.info("\n\n")
    log.info("stage 1: compress the model")
    control_file = "compress.json"
    with open(control_file, "w") as fp:
        json.dump(jdata, fp, indent=4)
    try:
        train(
            INPUT=control_file,
            init_model=None,
            restart=None,
            output=control_file,
            mpi_log=mpi_log,
            log_level=log_level,
            log_path=log_path,
            is_compress=True,
        )
    except GraphTooLargeError as e:
        raise RuntimeError(
            "The uniform step size of the tabulation's first table is %f, " 
            "which is too small. This leads to a very large graph size, "
            "exceeding protobuf's limitation (2 GB). You should try to "
            "increase the step size." % step
        ) from e

    # stage 2: freeze the model
    log.info("\n\n")
    log.info("stage 2: freeze the model")
    freeze(checkpoint_folder=checkpoint_folder, output=output, node_names=None)

    # stage 3: transfer the model
    log.info("\n\n")
    log.info("stage 3: transfer the model")
    transfer(old_model=input, raw_model=output, output=output)