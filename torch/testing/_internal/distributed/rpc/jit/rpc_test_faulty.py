from typing import Dict, Tuple

import torch
import torch.distributed.rpc as rpc
import torch.testing._internal.dist_utils as dist_utils
from torch import Tensor
from torch.testing._internal.dist_utils import (
    dist_init,
    get_timeout_error_regex,
    worker_name,
)
from torch.testing._internal.distributed.rpc.faulty_rpc_agent_test_fixture import (
    FaultyRpcAgentTestFixture,
)


@torch.jit.script
def two_args_two_kwargs(
    first_arg,
    second_arg,
    first_kwarg=torch.tensor([3, 3]),
    second_kwarg=torch.tensor([4, 4]),
):
    return first_arg + second_arg + first_kwarg + second_kwarg


@torch.jit.script
def rpc_async_call_remote_torchscript_in_torchscript(
    dst_worker_name: str, args: Tuple[Tensor, Tensor], kwargs: Dict[str, Tensor]
):
    fut = rpc.rpc_async(dst_worker_name, two_args_two_kwargs, args, kwargs)
    ret = fut.wait()
    return ret


@torch.jit.script
def rpc_async_call_with_timeout(
    dst_worker_name: str,
    args: Tuple[Tensor, Tensor],
    kwargs: Dict[str, Tensor],
    timeout: float,
):
    fut = rpc.rpc_async(dst_worker_name, two_args_two_kwargs, args, kwargs, timeout)
    ret = fut.wait()
    return ret


@torch.jit.script
def rpc_async_call_with_timeout_future_ret(
    dst_worker_name: str,
    args: Tuple[Tensor, Tensor],
    kwargs: Dict[str, Tensor],
    timeout: float,
):
    fut = rpc.rpc_async(dst_worker_name, two_args_two_kwargs, args, kwargs, timeout)
    return fut


@torch.jit.script
def rpc_async_call_future_ret(
    dst_worker_name: str, args: Tuple[Tensor, Tensor], kwargs: Dict[str, Tensor]
):
    fut = rpc.rpc_async(dst_worker_name, two_args_two_kwargs, args, kwargs)
    return fut


class JitFaultyAgentRpcTest(FaultyRpcAgentTestFixture):
    """
    Run tests for rpc_async in JIT under the faulty agent test fixture to test
    arbitrary timeouts.
    """
    @dist_init(faulty_messages=[], messages_to_delay={"SCRIPT_CALL": 1.5})
    def test_timeout_in_torchscript_function(self):
        # Call rpc_async + fut.wait() in torchscript function and ensure that
        # timeout is raised.
        if self.rank != 0:
            return

        dst_worker_name = worker_name((self.rank + 1) % self.world_size)

        args = (torch.tensor([1, 1]), torch.tensor([2, 2]))
        kwargs = {
            "first_kwarg": torch.tensor([2, 2]),
            "second_kwarg": torch.tensor([3, 3]),
        }
        expected_error = get_timeout_error_regex(
            dist_utils.TEST_CONFIG.rpc_backend_name
        )
        print("Test config is {}".format(dist_utils.TEST_CONFIG.rpc_backend_name))
        # Ensure that we get a timeout if we override the default timeout and
        # the RPC takes longer to execute.
        with self.assertRaisesRegex(RuntimeError, expected_error):
            rpc_async_call_with_timeout(dst_worker_name, args, kwargs, 0.5)

        # Ensure that we timeout if we don't specify a timeout but the default
        # is less than the RPC takes to execute.
        rpc._set_rpc_timeout(0.001)
        with self.assertRaisesRegex(RuntimeError, expected_error):
            rpc_async_call_remote_torchscript_in_torchscript(
                dst_worker_name, args, kwargs
            )

        # Ensure that we run to completion if zero timeout is specified.
        ret = rpc_async_call_with_timeout(dst_worker_name, args, kwargs, 0)
        self.assertEqual(ret, torch.tensor([8, 8]))
        # reset for clean shutdown
        rpc._set_rpc_timeout(rpc.constants.DEFAULT_RPC_TIMEOUT_SEC)

    @dist_init(faulty_messages=[], messages_to_delay={"SCRIPT_CALL": 1.5})
    def test_timeout_in_python(self):
        # Ensures timeouts are raised if we call rpc_async from within a
        # torchscript function, but wait on the future in python.
        if self.rank != 0:
            return

        dst_worker_name = worker_name((self.rank + 1) % self.world_size)
        args = (torch.tensor([1, 1]), torch.tensor([2, 2]))
        kwargs = {
            "first_kwarg": torch.tensor([2, 2]),
            "second_kwarg": torch.tensor([3, 3]),
        }
        expected_error = get_timeout_error_regex(
            dist_utils.TEST_CONFIG.rpc_backend_name
        )

        fut = rpc_async_call_with_timeout_future_ret(dst_worker_name, args, kwargs, 0.5)
        with self.assertRaisesRegex(RuntimeError, expected_error):
            fut.wait()

        # Ensure timeout if we don't specify but the default is less than the
        # RPC takes to execute.
        rpc._set_rpc_timeout(0.001)
        fut = rpc_async_call_future_ret(dst_worker_name, args, kwargs)
        with self.assertRaisesRegex(RuntimeError, expected_error):
            fut.wait()

        # Ensure run to completion if zero timeout is specified
        fut = rpc_async_call_with_timeout_future_ret(dst_worker_name, args, kwargs, 0)
        result = fut.wait()
        self.assertEqual(result, torch.tensor([8, 8]))
        # reset for clean shutdown
        rpc._set_rpc_timeout(rpc.constants.DEFAULT_RPC_TIMEOUT_SEC)
