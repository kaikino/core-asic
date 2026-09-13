# Verification

## cocotb suite (`make`)

`test_core.py`, `test_protocols.py`, `test_ethernet.py` and `test_random.py`
run under cocotb 2.0 with Icarus Verilog.  `proto_host.py` bit-bangs the SPI
host link and keeps the Python reference model (`tools/proto_ref.py`) in
lock-step with the RTL: after every clock it compares `uio_out`, `uio_oe`,
`uo_out[7:1]` and the timestamp, and at the end of a test the status word and
the trace buffer.  `protocols.py` holds independent UART/SPI/I2C peers and a
Manchester decoder that check the pin waveforms themselves.

```sh
cd test                    # the Makefile resolves paths from $PWD
make                       # everything
make COCOTB_TEST_MODULES=test_protocols
RANDOM_SEEDS=20 RANDOM_CYCLES=5000 make COCOTB_TEST_MODULES=test_random
cp ../runs/wokwi/final/nl/*.nl.v gate_level_netlist.v && PDK_ROOT=... make GATES=yes   # gate level
```

## Other checks

* `make smoke` runs the plain-Verilog smoke benches (no cocotb required).
* `python -m pytest test_assembler.py` tests assembler, disassembler and model.
* `sby -f ../formal/proto.sby` proves the pin-safety properties.
