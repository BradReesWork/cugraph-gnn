# Copyright (c) 2023-2024, NVIDIA CORPORATION.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest

from cugraph_pyg.nn import GATConv as CuGraphGATConv
from cugraph_pyg.utils.imports import package_available

ATOL = 1e-6


@pytest.mark.skipif(
    package_available("torch_geometric<2.5"), reason="Test requires pyg>=2.5"
)
@pytest.mark.parametrize("use_edge_index", [True, False])
@pytest.mark.parametrize("bias", [True, False])
@pytest.mark.parametrize("bipartite", [True, False])
@pytest.mark.parametrize("concat", [True, False])
@pytest.mark.parametrize("heads", [1, 2, 3, 5, 10, 16])
@pytest.mark.parametrize("max_num_neighbors", [8, None])
@pytest.mark.parametrize("use_edge_attr", [True, False])
@pytest.mark.parametrize("graph", ["basic_pyg_graph_1", "basic_pyg_graph_2"])
@pytest.mark.sg
def test_gat_conv_equality(
    use_edge_index,
    bias,
    bipartite,
    concat,
    heads,
    max_num_neighbors,
    use_edge_attr,
    graph,
    request,
):
    import torch
    from torch_geometric import EdgeIndex
    from torch_geometric.nn import GATConv

    torch.manual_seed(12345)
    edge_index, size = request.getfixturevalue(graph)
    edge_index = edge_index.cuda()

    if bipartite:
        in_channels = (5, 3)
        x = (
            torch.rand(size[0], in_channels[0]).cuda(),
            torch.rand(size[1], in_channels[1]).cuda(),
        )
    else:
        in_channels = 5
        x = torch.rand(size[0], in_channels).cuda()
    out_channels = 2

    if use_edge_attr:
        edge_dim = 3
        edge_attr = torch.rand(edge_index.size(1), edge_dim).cuda()
    else:
        edge_dim = edge_attr = None

    if use_edge_index:
        csc = EdgeIndex(edge_index, sparse_size=size)
    else:
        if use_edge_attr:
            csc, edge_attr_perm = CuGraphGATConv.to_csc(
                edge_index, size, edge_attr=edge_attr
            )
        else:
            csc = CuGraphGATConv.to_csc(edge_index, size)
            edge_attr_perm = None

    kwargs = dict(bias=bias, concat=concat, edge_dim=edge_dim)

    conv1 = GATConv(
        in_channels, out_channels, heads, add_self_loops=False, **kwargs
    ).cuda()
    conv2 = CuGraphGATConv(in_channels, out_channels, heads, **kwargs).cuda()

    out_dim = heads * out_channels
    with torch.no_grad():
        if bipartite:
            conv2.lin_src.weight.copy_(conv1.lin_src.weight)
            conv2.lin_dst.weight.copy_(conv1.lin_dst.weight)
        else:
            conv2.lin.weight.copy_(conv1.lin.weight)

        conv2.att[:out_dim].copy_(conv1.att_src.flatten())
        conv2.att[out_dim : 2 * out_dim].copy_(conv1.att_dst.flatten())
        if use_edge_attr:
            conv2.att[2 * out_dim :].copy_(conv1.att_edge.flatten())
            conv2.lin_edge.weight.copy_(conv1.lin_edge.weight)

    out1 = conv1(x, edge_index, edge_attr=edge_attr)
    if use_edge_index:
        out2 = conv2(x, csc, edge_attr=edge_attr, max_num_neighbors=max_num_neighbors)
    else:
        out2 = conv2(
            x, csc, edge_attr=edge_attr_perm, max_num_neighbors=max_num_neighbors
        )
    assert torch.allclose(out1, out2, atol=ATOL)

    grad_output = torch.rand_like(out1)
    out1.backward(grad_output)
    out2.backward(grad_output)

    if bipartite:
        assert torch.allclose(
            conv1.lin_src.weight.grad, conv2.lin_src.weight.grad, atol=ATOL
        )
        assert torch.allclose(
            conv1.lin_dst.weight.grad, conv2.lin_dst.weight.grad, atol=ATOL
        )
    else:
        assert torch.allclose(conv1.lin.weight.grad, conv2.lin.weight.grad, atol=ATOL)

    assert torch.allclose(
        conv1.att_src.grad.flatten(), conv2.att.grad[:out_dim], atol=ATOL
    )
    assert torch.allclose(
        conv1.att_dst.grad.flatten(), conv2.att.grad[out_dim : 2 * out_dim], atol=ATOL
    )

    if use_edge_attr:
        assert torch.allclose(
            conv1.att_edge.grad.flatten(), conv2.att.grad[2 * out_dim :], atol=ATOL
        )
        assert torch.allclose(
            conv1.lin_edge.weight.grad, conv2.lin_edge.weight.grad, atol=ATOL
        )

    if bias:
        assert torch.allclose(conv1.bias.grad, conv2.bias.grad, atol=ATOL)